import { useEffect, useMemo, useState } from "react";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { LocationStatusBadge } from "../../components/LocationStatusBadge";
import { PlatformBadge } from "../../components/PlatformBadge";
import { ScrollToTopButton } from "../../components/ScrollToTopButton";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { getLocationsIndex, type LocationIndexItem } from "../../lib/api";
import { formatDate, formatFinishTimeValue, pluralizeRu } from "../../lib/format";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { TableViewToggle, useTableView } from "../../components/tableUx/TableViewToggle";
import { useAdaptiveColumns, type AdaptiveColumn } from "../../components/tableUx/useAdaptiveColumns";

const PLATFORM_FILTERS = ["five_verst", "s95", "runpark"] as const;

type SortKey =
  | "name"
  | "city"
  | "events_count"
  | "finishers_total"
  | "avg_finishers"
  | "attendance_record"
  | "best_male"
  | "best_female"
  | "first_event_date";

// Колонки, которые логичнее открывать по возрастанию: алфавит и рекорды
// (у времени «лучше» = меньше).
const ASC_FIRST_KEYS: SortKey[] = ["name", "city", "best_male", "best_female"];
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
  // Меньше десяти человек — один знак после запятой: разница 4,2 и 4,8
  // для маленькой площадки существенна, для сотенной — шум.
  return value < 10 ? value.toFixed(1).replace(".", ",") : String(Math.round(value));
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
    case "attendance_record":
      return item.attendance_record_finishers;
    case "best_male":
      return item.best_male_time_sec;
    case "best_female":
      return item.best_female_time_sec;
    case "first_event_date":
      return item.first_event_date;
  }
}

// Краткий вид: колонки в порядке важности, а не в порядке вывода. Обязательный
// минимум — название, система и «Стартов»; дальше добавляем по мере ширины.
// Ширины совпадают с CSS (.loc-index-table): col-city 10rem, col-platform
// 6.5rem, col-metric 9.25rem, col-metric-wide 11.5rem, col-date 10rem.
const LOCATIONS_COLUMNS: AdaptiveColumn[] = [
  { key: "name", width: 220, required: true },
  { key: "platform", width: 104, required: true },
  { key: "events_count", width: 148, required: true },
  { key: "city", width: 160 },
  { key: "finishers_total", width: 148 },
  { key: "first_event_date", width: 160 },
  { key: "avg_finishers", width: 148 },
  { key: "attendance_record", width: 148 },
  { key: "best_male", width: 184 },
  { key: "best_female", width: 184 },
];

function LocationsTable({ items }: { items: LocationIndexItem[] }) {
  const [sort, setSort] = useState<SortState>({ key: "events_count", asc: false });
  const [tableView, setTableView] = useTableView("locationsIndex");
  // «Полно» — весь набор с горизонтальным скроллом. «Кратко» — столько колонок,
  // сколько влезает в ширину блока: на телефоне это три, на широком мониторе —
  // почти всё (решение Дмитрия 11.08.2026, вместо порога 820px).
  const adaptive = useAdaptiveColumns(LOCATIONS_COLUMNS);
  const showFull = tableView === "full";
  const show = (key: string) => showFull || adaptive.isVisible(key);

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
    return copy;
  }, [items, sort]);

  const toggleSort = (key: SortKey) => {
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
      <TableViewToggle value={tableView} onChange={setTableView} alwaysVisible />
      <TableWrap stickyFirstCol={showFull} outerRef={adaptive.measureRef}>
        <table
          className={`data-table data-table-layout-fixed loc-index-table${
            showFull ? "" : " data-table-short"
          }`}
          style={showFull ? undefined : { minWidth: adaptive.minWidth }}
        >
        <colgroup>
          <col />
          {show("city") && <col className="col-city" />}
          <col className="col-platform" />
          <col className="col-metric" />
          {show("finishers_total") && <col className="col-metric" />}
          {show("avg_finishers") && <col className="col-metric" />}
          {show("attendance_record") && <col className="col-metric" />}
          {show("best_male") && <col className="col-metric-wide" />}
          {show("best_female") && <col className="col-metric-wide" />}
          {show("first_event_date") && <col className="col-date" />}
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
                {...sortProps("first_event_date")}
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
              <tr key={item.identity_key}>
                <td className="td-location">
                  <a href={`/locations/${item.slug}`}>{item.name}</a>
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
                <td className="td-compact">{item.events_count || "—"}</td>
                {show("finishers_total") && (
                  <td className="td-compact">{item.finishers_total || "—"}</td>
                )}
                {show("avg_finishers") && <td className="td-compact">{formatAvgFinishers(item)}</td>}
                {show("attendance_record") && (
                  <td
                    className="td-compact"
                    title={
                      item.attendance_record_date
                        ? `Рекорд явки: ${formatDate(item.attendance_record_date)}`
                        : undefined
                    }
                  >
                    {item.attendance_record_finishers ?? "—"}
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
              </tr>
            ))
          )}
        </tbody>
      </table>
      </TableWrap>
    </>
  );
}

function LocationsIndexContent() {
  const [items, setItems] = useState<LocationIndexItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [platformFilter, setPlatformFilter] = useState<string | null>(null);
  const [showPaused, setShowPaused] = useState(false);

  useEffect(() => {
    getLocationsIndex()
      .then((data) => setItems(data.items))
      .catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить локации"));
  }, []);

  const filtered = useMemo(() => {
    if (!items) {
      return [];
    }
    const normalizedQuery = query.trim().toLowerCase();
    return items.filter((item) => {
      if (normalizedQuery && !matchesQuery(item, normalizedQuery)) {
        return false;
      }
      if (platformFilter && !item.platform_codes.includes(platformFilter)) {
        return false;
      }
      if (!showPaused && (item.is_paused || item.is_cancelled)) {
        return false;
      }
      return true;
    });
  }, [items, query, platformFilter, showPaused]);

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
        <div className="loc-index-toolbar">
          <input
            className="input loc-index-search"
            type="search"
            placeholder="Поиск: название, город или регион"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <div className="loc-index-filters">
            {PLATFORM_FILTERS.map((code) => (
              <button
                key={code}
                type="button"
                className={`btn btn-ghost btn-sm${platformFilter === code ? " loc-hist-mode-active" : ""}`}
                onClick={() => setPlatformFilter(platformFilter === code ? null : code)}
              >
                <PlatformBadge code={code} />
              </button>
            ))}
            <label className="loc-index-paused">
              <input
                type="checkbox"
                checked={showPaused}
                onChange={(event) => setShowPaused(event.target.checked)}
              />{" "}
              показывать неактивные
            </label>
          </div>
        </div>

        {error && <div className="card error"><p>{error}</p></div>}
        {!items && !error && <p className="muted">Загрузка…</p>}

        {items && (
          <>
            <p className="muted loc-index-count">
              {pluralizeRu(filtered.length, ["локация", "локации", "локаций"])}
            </p>
            <LocationsTable items={filtered} />
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
