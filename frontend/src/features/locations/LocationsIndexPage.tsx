import { useEffect, useMemo, useState } from "react";
import { useCachedResource } from "../../hooks/useCachedResource";
import { useRestorableState } from "../../hooks/useRestorableState";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { LocationStatusBadge } from "../../components/LocationStatusBadge";
import { PlatformBadge } from "../../components/PlatformBadge";
import { PlatformFilter } from "../../components/filters/PlatformFilter";
import { ScrollToTopButton } from "../../components/ScrollToTopButton";
import {
  FilterGroup,
  FilterPanel,
  FilterRow,
  FilterSearch,
} from "../../components/filters/FilterPanel";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { getHomeLocation, getLocationsIndex, type LocationIndexItem } from "../../lib/api";
import { useOptionalUser } from "../../lib/useOptionalUser";
import {
  formatDate,
  formatFinishTimeValue,
  formatInt,
  platformCodeLabel,
  pluralizeRu,
} from "../../lib/format";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { TableViewToggle } from "../../components/tableUx/TableViewToggle";
import { useTableColumns, type TableColumns } from "../../components/tableUx/useTableColumns";
import type { AdaptiveColumn } from "../../components/tableUx/useAdaptiveColumns";

const PLATFORM_FILTERS = ["five_verst", "s95", "runpark"] as const;

type SortKey =
  | "name"
  | "city"
  | "events_count"
  | "finishers_total"
  | "avg_finishers"
  | "avg_finish_time"
  | "attendance_record"
  | "best_male"
  | "best_female"
  | "first_event_date"
  | "first_event_date_in_system";

// Колонки, которые логичнее открывать по возрастанию: алфавит и рекорды
// (у времени «лучше» = меньше).
const ASC_FIRST_KEYS: SortKey[] = [
  "name",
  "city",
  "best_male",
  "best_female",
  // Среднее время: «лучше» — меньше, как и у рекордов.
  "avg_finish_time",
];
type SortState = { key: SortKey; asc: boolean };

/** Средняя явка — сколько человек финиширует на этой площадке в обычную субботу. */
function avgFinishers(item: LocationIndexItem): number | null {
  if (!item.events_count || !item.finishers_total) {
    return null;
  }
  return item.finishers_total / item.events_count;
}

/** Рекорд локации — как в журнале протоколов: без ведущих «00:» часов. */
function formatRecord(display: string | null, sec: number | null): string {
  if (sec === null && !display) {
    return "—";
  }
  return formatFinishTimeValue(display, sec).replace(/^00:/, "");
}

function formatAvgFinishers(item: LocationIndexItem): string {
  const value = avgFinishers(item);
  if (value === null) {
    return "—";
  }
  // Всегда с десятыми (просьба Дмитрия 21.09.2026): у соседних локаций явка
  // часто различается меньше чем на человека, целые их не разводят.
  return value.toFixed(1).replace(".", ",");
}

function matchesQuery(item: LocationIndexItem, query: string): boolean {
  const haystack = [item.name, item.city, item.region, item.country]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(query);
}

function sortValue(item: LocationIndexItem, key: SortKey): number | string | null {
  switch (key) {
    case "name":
      return item.name.toLowerCase();
    case "city":
      return (item.city ?? "").toLowerCase();
    case "events_count":
      return item.events_count;
    case "finishers_total":
      return item.finishers_total;
    case "avg_finishers":
      return avgFinishers(item);
    case "avg_finish_time":
      return item.avg_finish_time_sec;
    case "attendance_record":
      return item.attendance_record_finishers;
    case "best_male":
      return item.best_male_time_sec;
    case "best_female":
      return item.best_female_time_sec;
    case "first_event_date":
      return item.first_event_date;
    case "first_event_date_in_system":
      return item.first_event_date_in_system;
  }
}

// Краткий вид: колонки в порядке важности, а не в порядке вывода. Обязательный
// минимум — название, система и «Стартов»; дальше добавляем по мере ширины.
// Ширины совпадают с CSS (.loc-index-table): col-city 10rem, col-platform
// 6.5rem, col-metric 9.25rem, col-metric-wide 11.5rem, col-date 10rem,
// col-date-wide 13rem.
//
// Обе даты первого старта — в самом хвосте (решение Дмитрия 23.08.2026): в
// краткий вид важнее пустить явку и рекорды, а «когда здесь начали бегать» —
// вопрос разовый, за ним не грех переключиться в «Полно».
const LOCATIONS_COLUMNS: AdaptiveColumn[] = [
  { key: "name", width: 170, required: true },
  { key: "platform", width: 104, required: true },
  { key: "events_count", width: 148, required: true },
  { key: "city", width: 160 },
  { key: "finishers_total", width: 148 },
  { key: "avg_finishers", width: 148 },
  { key: "avg_finish_time", width: 164 },
  { key: "attendance_record", width: 148 },
  { key: "best_male", width: 184 },
  { key: "best_female", width: 184 },
  { key: "first_event_date", width: 160 },
  { key: "first_event_date_in_system", width: 200 },
];

function LocationsTable({
  items,
  tableColumns,
  homeIdentityKey,
}: {
  items: LocationIndexItem[];
  tableColumns: TableColumns;
  homeIdentityKey: string | null;
}) {
  const [sort, setSort] = useRestorableState<SortState>("locations.sort", {
    key: "events_count",
    asc: false,
  });
  // Пока человек не трогал столбцы, «моя» локация приколота сверху. Как только
  // он сортирует сам — открепляем: он смотрит на порядок, а не на свою строку.
  const [homePinned, setHomePinned] = useRestorableState<boolean>("locations.homePinned", true);
  const showFull = tableColumns.showFull;
  const show = tableColumns.show;

  const sorted = useMemo(() => {
    const copy = [...items];
    copy.sort((a, b) => {
      const left = sortValue(a, sort.key);
      const right = sortValue(b, sort.key);
      if (left === right) {
        return a.name.localeCompare(b.name, "ru");
      }
      if (left === null) {
        return 1;
      }
      if (right === null) {
        return -1;
      }
      const compare =
        typeof left === "string" && typeof right === "string"
          ? left.localeCompare(right, "ru")
          : left < right
            ? -1
            : 1;
      return sort.asc ? compare : -compare;
    });
    // «Моя» локация поднята наверх и на виду — как своя строка в рейтингах
    // (просьба из бэклога сайта). Только если она проходит текущие фильтры:
    // при поиске по другому названию поднимать её наверх было бы враньём.
    // И только пока порядок наш: сортировку человек задал сам — не мешаем.
    const homeIndex =
      homeIdentityKey && homePinned
        ? copy.findIndex((item) => item.identity_key === homeIdentityKey)
        : -1;
    if (homeIndex > 0) {
      const [home] = copy.splice(homeIndex, 1);
      copy.unshift(home);
    }
    return copy;
  }, [items, sort, homeIdentityKey, homePinned]);

  const toggleSort = (key: SortKey) => {
    setHomePinned(false);
    setSort((current) =>
      current.key === key ? { key, asc: !current.asc } : { key, asc: ASC_FIRST_KEYS.includes(key) },
    );
  };

  const sortProps = (key: SortKey) => ({
    filterable: false,
    sortActive: sort.key === key,
    sortAsc: sort.asc,
    onSort: () => toggleSort(key),
  });

  const visibleCount = LOCATIONS_COLUMNS.filter((column) => show(column.key)).length;

  return (
    <>
      <TableWrap stickyFirstCol={showFull} outerRef={tableColumns.measureRef}>
        <table
          className={`data-table data-table-layout-fixed loc-index-table${
            showFull ? "" : " data-table-short"
          }`}
          style={{ minWidth: tableColumns.minWidth }}
        >
        <colgroup>
          <col />
          {show("city") && <col className="col-city" />}
          <col className="col-platform" />
          <col className="col-metric" />
          {show("finishers_total") && <col className="col-metric" />}
          {show("avg_finishers") && <col className="col-metric" />}
          {show("avg_finish_time") && <col className="col-metric" />}
          {show("attendance_record") && <col className="col-metric" />}
          {show("best_male") && <col className="col-metric-wide" />}
          {show("best_female") && <col className="col-metric-wide" />}
          {show("first_event_date") && <col className="col-date" />}
          {show("first_event_date_in_system") && <col className="col-date-wide" />}
        </colgroup>
        <thead>
          <tr>
            <ColumnHeader label="Локация" {...sortProps("name")} />
            {show("city") && <ColumnHeader label="Город" {...sortProps("city")} />}
            <ColumnHeader label="Система" filterable={false} />
            <ColumnHeader
              label="Стартов"
              hint="Мероприятий проведено за всё время"
              {...sortProps("events_count")}
            />
            {show("finishers_total") && (
              <ColumnHeader
                label="Финишей"
                hint="Финишей за всё время"
                {...sortProps("finishers_total")}
              />
            )}
            {show("avg_finishers") && (
              <ColumnHeader
                label="В среднем"
                hint="Средняя явка: финишей за всё время ÷ число стартов"
                {...sortProps("avg_finishers")}
              />
            )}
            {show("avg_finish_time") && (
              <ColumnHeader
                label="Среднее время"
                hint="Среднее время финишёра за всю историю площадки"
                {...sortProps("avg_finish_time")}
              />
            )}
            {show("attendance_record") && (
              <ColumnHeader
                label="Рекорд явки"
                hint="Максимум финишей за один старт (в подсказке — дата)"
                {...sortProps("attendance_record")}
              />
            )}
            {show("best_male") && (
              <ColumnHeader
                label="Лучшее время (М)"
                hint="Рекорд локации (LR): лучшее время мужчины здесь за всю историю"
                {...sortProps("best_male")}
              />
            )}
            {show("best_female") && (
              <ColumnHeader
                label="Лучшее время (Ж)"
                hint="Рекорд локации (LR): лучшее время женщины здесь за всю историю"
                {...sortProps("best_female")}
              />
            )}
            {show("first_event_date") && (
              <ColumnHeader
                label="Первый старт"
                hint="Самый первый старт площадки — в любой системе, включая parkrun-эпоху"
                {...sortProps("first_event_date")}
              />
            )}
            {show("first_event_date_in_system") && (
              <ColumnHeader
                label="Первый старт в системе"
                hint="Когда площадка начала работать в нынешней системе. У переехавших из parkrun-эпохи эта дата на годы позже сквозной"
                {...sortProps("first_event_date_in_system")}
              />
            )}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 ? (
            <tr>
              <td colSpan={visibleCount} className="table-empty-cell">
                <span className="muted">Нет локаций по фильтрам</span>
              </td>
            </tr>
          ) : (
            sorted.map((item) => (
              <tr
                key={item.identity_key}
                className={item.identity_key === homeIdentityKey ? "loc-index-row-home" : undefined}
              >
                <td className="td-location">
                  <a href={`/locations/${item.slug}`}>{item.name}</a>
                  {item.identity_key === homeIdentityKey && (
                    // Плашка — ссылка в настройки: «моя» вызывает вопрос «а
                    // почему эта?», и ответ вместе с возможностью сменить
                    // лежат в одном месте (правка Дмитрия 17.09.2026).
                    <a
                      className="location-status-badge loc-index-home-badge"
                      href="/settings#home-location"
                      title="Ваша домашняя локация: от неё считается дальность стартов, по ней вы «свой» на локации. Выбирается по вашим пробежкам автоматически — нажмите, чтобы посмотреть или сменить в настройках"
                    >
                      🏠 моя
                    </a>
                  )}
                  <LocationStatusBadge isPaused={item.is_paused} isCancelled={item.is_cancelled} />
                </td>
                {show("city") && (
                  <td className="muted">
                    {item.city ?? "—"}
                    {item.country && item.country !== "Россия" ? ` · ${item.country}` : ""}
                  </td>
                )}
                <td>
                  <span className="loc-index-platforms">
                    {item.platform_codes.map((code) => (
                      <PlatformBadge key={code} code={code} />
                    ))}
                  </span>
                </td>
                <td className="td-compact">{item.events_count ? formatInt(item.events_count) : "—"}</td>
                {show("finishers_total") && (
                  <td className="td-compact">{item.finishers_total ? formatInt(item.finishers_total) : "—"}</td>
                )}
                {show("avg_finishers") && <td className="td-compact">{formatAvgFinishers(item)}</td>}
                {show("avg_finish_time") && (
                  <td className="td-compact">{item.avg_finish_time_display ?? "—"}</td>
                )}
                {show("attendance_record") && (
                  <td
                    className="td-compact"
                    title={
                      item.attendance_record_date
                        ? `Рекорд явки: ${formatDate(item.attendance_record_date)}`
                        : undefined
                    }
                  >
                    {item.attendance_record_finishers != null ? formatInt(item.attendance_record_finishers) : "—"}
                  </td>
                )}
                {show("best_male") && (
                  <td className="td-compact">
                    {formatRecord(item.best_male_time_display, item.best_male_time_sec)}
                  </td>
                )}
                {show("best_female") && (
                  <td className="td-compact">
                    {formatRecord(item.best_female_time_display, item.best_female_time_sec)}
                  </td>
                )}
                {show("first_event_date") && (
                  <td>{item.first_event_date ? formatDate(item.first_event_date) : "—"}</td>
                )}
                {show("first_event_date_in_system") && (
                  <td>
                    {item.first_event_date_in_system ? (
                      // Обёртка-span, а не flex на самой ячейке: td с display:flex
                      // перестаёт быть табличной ячейкой и ломает выравнивание
                      // (так же сделан столбец систем выше).
                      <span className="loc-index-first-in-system">
                        {formatDate(item.first_event_date_in_system)}
                        {/* Систему подписываем прямо в ячейке: колонка отвечает
                            на вопрос «когда здесь начались 5 вёрст», и без имени
                            системы ответ половинчатый. */}
                        {item.first_event_system_code && (
                          <PlatformBadge code={item.first_event_system_code} />
                        )}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                )}
              </tr>
            ))
          )}
        </tbody>
      </table>
      </TableWrap>
    </>
  );
}

/**
 * Серии стартов — формат, а не место: «Старты сообществ» 5 вёрст, «С95 и
 * друзья», «S95 & Friends». Координат у них нет, трасса каждый раз новая, и в
 * алфавите площадок они стояли однодневками вроде «Зелёные 5 км». Отдельный
 * блок отвечает на вопрос «что это вообще» одной строкой, а финиши оттуда
 * по-прежнему считаются людям в личные итоги.
 */
function SeriesBlock({ items }: { items: LocationIndexItem[] }) {
  if (items.length === 0) {
    return null;
  }
  return (
    <section className="loc-series">
      <h2 className="loc-series-title">Серии стартов</h2>
      <p className="muted loc-series-note">
        Не площадки, а форматы: старты проходят нерегулярно и каждый раз в новом месте.
        Финиши с них идут в личный счёт, но в карту, туризм и рейтинги локаций не попадают.
      </p>
      <TableWrap>
        <table className="data-table loc-series-table">
          <thead>
            <tr>
              <ColumnHeader label="Серия" filterable={false} />
              <ColumnHeader label="Система" filterable={false} />
              <ColumnHeader label="Стартов" filterable={false} />
              <ColumnHeader label="Финишей" filterable={false} />
              <ColumnHeader label="Первый старт" filterable={false} />
              <ColumnHeader label="Последний" filterable={false} />
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.identity_key}>
                <td className="td-location">
                  <a href={`/locations/${item.slug}`}>{item.name}</a>
                </td>
                <td>
                  <span className="loc-index-platforms">
                    {item.platform_codes.map((code) => (
                      <PlatformBadge key={code} code={code} />
                    ))}
                  </span>
                </td>
                <td className="td-compact">{item.events_count ? formatInt(item.events_count) : "—"}</td>
                <td className="td-compact">
                  {item.finishers_total ? formatInt(item.finishers_total) : "—"}
                </td>
                <td>{item.first_event_date ? formatDate(item.first_event_date) : "—"}</td>
                <td>{item.last_event_date ? formatDate(item.last_event_date) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
    </section>
  );
}

function LocationsIndexContent() {
  // «Полно» — весь набор с горизонтальным скроллом. «Кратко» — столько колонок,
  // сколько влезает в ширину блока (решение Дмитрия 11.08.2026). Состояние
  // живёт здесь, а не в самой таблице: сегмент «Кратко | Полно» стоит в общей
  // панели фильтров над ней (правка Дмитрия 30.08.2026).
  const tableColumns = useTableColumns(LOCATIONS_COLUMNS);
  // Ответ и фильтры переживают уход и «назад» (см. hooks/useCachedResource,
  // hooks/useRestorableState): каталог — витрина, из него уходят на площадку
  // и возвращаются к тому же месту списка.
  const index = useCachedResource("locations:index", getLocationsIndex, [], {
    errorText: "Не удалось загрузить локации",
  });
  const items = index.data?.items ?? null;
  const series = index.data?.series ?? [];
  const error = index.error;
  // ?q= в адресе — фильтр, с которым сюда привели: «Все локации: Москва (41)»
  // из поиска по сайту. Снимок записи истории (возврат «назад») важнее.
  const [query, setQuery] = useRestorableState(
    "locations.query",
    () => new URLSearchParams(window.location.search).get("q")?.trim() ?? "",
  );
  // Мультивыбор: систем можно отметить сколько угодно, пустое множество —
  // «Все» (правка Дмитрия 01.09.2026; раньше выбиралась ровно одна).
  // Одна система за раз: каталог смотрят «покажи мне 5 вёрст», а не «5 вёрст
  // и S95 вместе» — мультивыбор здесь только путал (Дмитрий 02.09.2026).
  const [platform, setPlatform] = useRestorableState("locations.platform", "all");
  const [showPaused, setShowPaused] = useRestorableState("locations.paused", false);
  // Домашняя локация зрителя — её строка поднимается наверх и подсвечивается.
  // Спрашиваем отдельным запросом: каталог кэшируется одним блобом на всех,
  // личным данным в этом кэше не место.
  const viewer = useOptionalUser();
  const [homeIdentityKey, setHomeIdentityKey] = useState<string | null>(null);
  useEffect(() => {
    if (!viewer) {
      setHomeIdentityKey(null);
      return;
    }
    let cancelled = false;
    getHomeLocation()
      .then((payload) => {
        if (!cancelled) {
          setHomeIdentityKey(payload.location?.catalog_identity_key ?? null);
        }
      })
      .catch(() => {
        // Не смогли — каталог просто останется без отметки «моя».
      });
    return () => {
      cancelled = true;
    };
  }, [viewer]);

  const filtered = useMemo(() => {
    if (!items) {
      return [];
    }
    const normalizedQuery = query.trim().toLowerCase();
    return items.filter((item) => {
      if (normalizedQuery && !matchesQuery(item, normalizedQuery)) {
        return false;
      }
      if (platform !== "all" && !item.platform_codes.includes(platform)) {
        return false;
      }
      if (!showPaused && (item.is_paused || item.is_cancelled)) {
        return false;
      }
      return true;
    });
  }, [items, query, platform, showPaused]);

  // Серии фильтруем тем же поиском и той же системой: «покажи мне s95» не
  // должно оставлять внизу «Старты сообществ».
  const filteredSeries = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return series.filter((item) => {
      if (normalizedQuery && !matchesQuery(item, normalizedQuery)) {
        return false;
      }
      if (platform !== "all" && !item.platform_codes.includes(platform)) {
        return false;
      }
      return true;
    });
  }, [series, query, platform]);

  return (
    <PortalSectionShell sidebar={{ active: "locations" }}>
      <header className="loc-header">
        <div className="loc-header-title">
          <h1>Локации</h1>
        </div>
        <p className="muted loc-header-place">
          Все площадки субботних стартов: цифры, рекорды и история каждой локации.{" "}
          <a href="/results">Результаты последней субботы →</a>
        </p>
      </header>

      <section className="card loc-section loc-wide-page">
        <FilterPanel>
          <FilterRow>
            <PlatformFilter
              mode="single"
              value={platform}
              onChange={setPlatform}
              options={PLATFORM_FILTERS.map((code) => ({
                code,
                label: platformCodeLabel(code),
              }))}
            />
            <FilterGroup label="Показывать">
              <label className="loc-index-paused">
                <input
                  type="checkbox"
                  checked={showPaused}
                  onChange={(event) => setShowPaused(event.target.checked)}
                />{" "}
                неактивные
              </label>
            </FilterGroup>
            {tableColumns.hasToggle && (
              <FilterGroup label="Колонки">
                <TableViewToggle columns={tableColumns} inline />
              </FilterGroup>
            )}
            <FilterGroup label="Поиск" trailing>
              <FilterSearch
                value={query}
                onChange={setQuery}
                placeholder="Поиск: название, город или регион"
              />
            </FilterGroup>
          </FilterRow>
        </FilterPanel>

        {error && <div className="card error"><p>{error}</p></div>}
        {!items && !error && <p className="muted">Загрузка…</p>}

        {items && (
          <>
            <p className="muted loc-index-count">
              {pluralizeRu(filtered.length, ["локация", "локации", "локаций"])}
            </p>
            <LocationsTable
              items={filtered}
              tableColumns={tableColumns}
              homeIdentityKey={homeIdentityKey}
            />
            <SeriesBlock items={filteredSeries} />
          </>
        )}
      </section>
      <ScrollToTopButton />
    </PortalSectionShell>
  );
}

// Каталог открыт без логина: локации — публичная витрина сайта.
export function LocationsIndexPage() {
  return <LocationsIndexContent />;
}
