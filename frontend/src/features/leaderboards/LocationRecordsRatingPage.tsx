import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PlatformBadge } from "../../components/PlatformBadge";
import { GenderFilter } from "../../components/filters/GenderFilter";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { useNarrowViewport } from "../../components/tableUx/useNarrowViewport";
import { formatDate } from "../../lib/format";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { RatingsLoginBanner } from "./RatingsLoginBanner";
import {
  LOCATION_RECORDS_PLATFORM_LABELS,
  getLocationRecords,
  type LocationRecordRow,
  type LocationRecordsGender,
  type LocationRecordsPlatform,
  type LocationRecordsResponse,
  type LocationRecordsScope,
} from "./locationRecordsApi";

const PAGE_STEP = 100;

const SCOPE_TABS: { value: LocationRecordsScope; label: string }[] = [
  { value: "absolute", label: "Абсолютный" },
  { value: "age_group", label: "Возрастной" },
];

const GENDER_TABS: { value: LocationRecordsGender; label: string }[] = [
  { value: "male", label: "Мужчины" },
  { value: "female", label: "Женщины" },
];

const SCOPE_HINT =
  "Абсолютный зачёт — лучшее время локации среди мужчин или женщин за всю её историю, " +
  "включая parkrun-эпоху. Возрастной — рекорд в выбранной категории, считается только " +
  "по 5 вёрст.";

const PLATFORM_HINT =
  "По умолчанию рекорд считается сквозь все системы локации. Фильтр показывает рекорд " +
  "внутри одной системы — например, лучшее время эпохи parkrun отдельно от нынешней.";

/** Часы в «01:02:03» не нужны: пять километров быстрее часа у всех рекордсменов. */
function stripLeadingHours(display: string | null): string {
  if (!display) {
    return "—";
  }
  return display.startsWith("00:") ? display.slice(3) : display;
}

function RunnerName({ name, handle }: { name: string | null; handle: string | null }) {
  const label = name?.trim() || "—";
  if (!handle || label === "—") {
    return <>{label}</>;
  }
  return <a href={`/users/${encodeURIComponent(handle)}`}>{label}</a>;
}

function RecordDate({ row }: { row: LocationRecordRow }) {
  if (!row.event_date) {
    return <>—</>;
  }
  const label = formatDate(row.event_date);
  return row.protocol_url ? <a href={row.protocol_url}>{label}</a> : <>{label}</>;
}

function LocationCell({ row }: { row: LocationRecordRow }) {
  // Город и регион — в подпись: в Москве и Петербурге локаций десятки, а
  // «Сосновка» есть и там, и в Кирове.
  const subtitle =
    row.city && row.region && row.city !== row.region
      ? `${row.city}, ${row.region}`
      : (row.city ?? row.region);
  return (
    <>
      <a href={`/locations/${row.slug}`}>{row.name}</a>
      {subtitle && <div className="muted lb-locrec-place">{subtitle}</div>}
    </>
  );
}

type SortKey = "time" | "date";

type SortState = { key: SortKey; direction: "asc" | "desc" };

/** Направление по умолчанию: рекорд — от быстрого, дата — от свежей. */
const DEFAULT_DIRECTION: Record<SortKey, SortState["direction"]> = {
  time: "asc",
  date: "desc",
};

function sortValue(row: LocationRecordRow, key: SortKey): number | null {
  if (key === "time") {
    return row.finish_time_sec;
  }
  // Площадка без даты рекорда (протокол parkrun-эры без дня): null, а «в самый
  // конец при любом направлении» обеспечивает компаратор — бесконечность здесь
  // попадала бы в начало при asc, а пара таких строк давала NaN в разности.
  return row.event_date ? Date.parse(row.event_date) : null;
}

function SortableHeader({
  label,
  sortKey,
  sort,
  onSort,
  className,
}: {
  label: string;
  sortKey: SortKey;
  sort: SortState;
  onSort: (key: SortKey) => void;
  className?: string;
}) {
  const active = sort.key === sortKey;
  return (
    <th
      className={`${className ?? ""} lb-sortable${active ? " lb-sorted" : ""}`}
      onClick={() => onSort(sortKey)}
      title="Сортировать по этому столбцу"
      aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}
      // Тап по заголовку сортирует — подсказку тач-режима здесь не показываем.
      data-tap-tooltip="off"
    >
      {label}
      <span className="lb-sort-mark" aria-hidden>
        {active ? (sort.direction === "asc" ? "▴" : "▾") : "↕"}
      </span>
    </th>
  );
}

function matchesQuery(row: LocationRecordRow, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) {
    return true;
  }
  return [row.name, row.city, row.region, row.runner_name]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(needle));
}

export function LocationRecordsRatingPage() {
  const narrowViewport = useNarrowViewport();
  const [data, setData] = useState<LocationRecordsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [visibleCount, setVisibleCount] = useState(PAGE_STEP);
  const [query, setQuery] = useState("");
  // Таблица приходит отсортированной по времени рекорда (оно же место), но её
  // читают и «по свежести»: чей рекорд поставлен недавно, а чей держится годами.
  const [sort, setSort] = useState<SortState>({ key: "time", direction: "asc" });
  // Таблицу листают вбок, поэтому её обёртка — скролл-контейнер, и настоящий
  // sticky-thead прилипает к ней, а не к окну: при прокрутке шапка уезжала
  // вверх и оказывалась ниже уже проехавших строк. Копия шапки — общий приём
  // широких таблиц сайта (рейтинги, протокол, журнал стартов).
  const attachFloatingHead = useFloatingTableHead();

  // Стартовые фильтры — из ссылки: рейтинг конкретной группы кидают в чат, и
  // адрес обязан открывать ровно то, что человек видел.
  const initial = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    const scope = params.get("scope") === "age_group" ? "age_group" : "absolute";
    const genderParam = params.get("gender");
    const gender: LocationRecordsGender | null =
      genderParam === "male" || genderParam === "female" ? genderParam : null;
    return {
      scope: scope as LocationRecordsScope,
      gender,
      ageGroup: params.get("group"),
      platform: (params.get("platform") ?? "all") as LocationRecordsPlatform,
    };
  }, []);

  const [scope, setScope] = useState<LocationRecordsScope>(initial.scope);
  // null — «зритель ничего не выбирал»: тогда пол и ступень подставит бэкенд по
  // его последней пробежке на 5 вёрст.
  const [gender, setGender] = useState<LocationRecordsGender | null>(initial.gender);
  const [ageGroup, setAgeGroup] = useState<string | null>(initial.ageGroup);
  const [platform, setPlatform] = useState<LocationRecordsPlatform>(initial.platform);

  // requestId отсекает устаревшие ответы: без него медленный ответ прежнего
  // среза перезаписывал бы свежий — вместе с только что выбранным полом
  // (setGender ниже) и адресом страницы.
  const requestIdRef = useRef(0);

  const load = useCallback(async () => {
    const requestId = ++requestIdRef.current;
    setLoading(true);
    setError(null);
    try {
      const payload = await getLocationRecords({ scope, gender, ageGroup, platform });
      if (requestId !== requestIdRef.current) {
        return;
      }
      setData(payload);
      // Что бэкенд выбрал за нас (своя ступень зрителя, ступень по умолчанию)
      // — сразу становится текущим выбором, иначе селектор показывал бы пустоту.
      setGender(payload.gender);
      if (payload.scope === "age_group") {
        setAgeGroup(payload.age_group);
      }
      setVisibleCount(PAGE_STEP);
    } catch (loadError) {
      if (requestId === requestIdRef.current) {
        setError(loadError instanceof Error ? loadError.message : "Не удалось загрузить рейтинг");
      }
    } finally {
      if (requestId === requestIdRef.current) {
        setLoading(false);
      }
    }
    // ageGroup специально не в зависимостях: он меняется и ответом сервера,
    // иначе загрузка зациклилась бы. Смена группы руками грузит данные сама.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, gender, platform]);

  useEffect(() => {
    void load();
  }, [load]);

  // Ссылку держим в актуальном состоянии без перезагрузки страницы.
  useEffect(() => {
    if (!data) {
      return;
    }
    const url = new URL(window.location.href);
    url.searchParams.set("scope", data.scope);
    url.searchParams.set("gender", data.gender);
    if (data.scope === "age_group" && data.age_group) {
      url.searchParams.set("group", data.age_group);
    } else {
      url.searchParams.delete("group");
    }
    if (data.scope === "absolute" && data.platform !== "all") {
      url.searchParams.set("platform", data.platform);
    } else {
      url.searchParams.delete("platform");
    }
    window.history.replaceState(null, "", url.toString());
  }, [data]);

  const changeAgeGroup = useCallback(
    async (value: string) => {
      const requestId = ++requestIdRef.current;
      setAgeGroup(value);
      setLoading(true);
      setError(null);
      try {
        const payload = await getLocationRecords({ scope: "age_group", gender, ageGroup: value, platform });
        if (requestId === requestIdRef.current) {
          setData(payload);
          setVisibleCount(PAGE_STEP);
        }
      } catch (loadError) {
        if (requestId === requestIdRef.current) {
          setError(loadError instanceof Error ? loadError.message : "Не удалось загрузить рейтинг");
        }
      } finally {
        if (requestId === requestIdRef.current) {
          setLoading(false);
        }
      }
    },
    [gender, platform],
  );

  const rows = useMemo(() => {
    const filtered = (data?.rows ?? []).filter((row) => matchesQuery(row, query));
    const factor = sort.direction === "asc" ? 1 : -1;
    return [...filtered].sort((left, right) => {
      const leftValue = sortValue(left, sort.key);
      const rightValue = sortValue(right, sort.key);
      // Строки без значения — в самый конец при любом направлении, между
      // собой — по месту в зачёте.
      if (leftValue === null || rightValue === null) {
        if (leftValue === rightValue) {
          return left.place - right.place;
        }
        return leftValue === null ? 1 : -1;
      }
      const delta = leftValue - rightValue;
      return delta !== 0 ? delta * factor : left.place - right.place;
    });
  }, [data, query, sort]);

  // Повторный клик по той же колонке переворачивает порядок, по новой —
  // ставит осмысленное направление по умолчанию.
  const toggleSort = useCallback((key: SortKey) => {
    setSort((current) =>
      current.key === key
        ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
        : { key, direction: DEFAULT_DIRECTION[key] },
    );
  }, []);
  const visibleRows = rows.slice(0, visibleCount);

  return (
    <PortalSectionShell sidebar={{ active: "ratings" }}>
      <div className="lb-page">
        <nav className="lb-breadcrumb">
          <a href="/ratings">← Все рейтинги</a>
          <span aria-hidden> / </span>
          <span>Локации · Рекорды локаций</span>
        </nav>

        <header className="lb-header">
          <h1>Рекорды локаций</h1>
          <p className="lb-description">
            Лучшее время каждой локации: в абсолютном зачёте — среди мужчин или женщин, в
            возрастном — внутри выбранной категории.
          </p>
        </header>

        <RatingsLoginBanner />

        <div className="lb-controls-row lb-locrec-controls">
          <div className="lb-controls-left">
            <div className="lb-visits">
              <span className="lb-visits-label">
                Зачёт{" "}
                <StatHintTooltip text={SCOPE_HINT}>
                  <span aria-label="Как считается">ⓘ</span>
                </StatHintTooltip>
              </span>
              <div className="lb-gender-tabs" role="group" aria-label="Зачёт">
                {SCOPE_TABS.map((tab) => (
                  <button
                    key={tab.value}
                    type="button"
                    aria-pressed={scope === tab.value}
                    className={`lb-gender-tab${scope === tab.value ? " lb-gender-tab-active" : ""}`}
                    onClick={() => setScope(tab.value)}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            </div>

            <GenderFilter
              value={gender ?? "male"}
              onChange={(next) => setGender(next as LocationRecordsGender)}
              options={GENDER_TABS.map((tab) => ({ value: tab.value }))}
            />

            {scope === "age_group" && data && data.age_groups.length > 0 && (
              <div className="lb-visits">
                <span className="lb-visits-label">Группа</span>
                {/* Тот же контрол, что фильтрует возрастные группы в протоколе
                    локации (.protocol-age-select) — не заводим второй вид
                    выпадающего списка ради одной страницы. */}
                <select
                  className="protocol-age-select lb-locrec-select"
                  value={ageGroup ?? data.age_group ?? ""}
                  onChange={(event) => void changeAgeGroup(event.target.value)}
                  aria-label="Возрастная группа"
                >
                  {data.age_groups.map((group) => (
                    <option key={group.key} value={group.age_group}>
                      {group.age_group}
                      {data.viewer_age_group === group.age_group && data.viewer_gender === data.gender
                        ? " — ваша"
                        : ""}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {scope === "absolute" && data && (
              <div className="lb-visits">
                <span className="lb-visits-label">
                  Система{" "}
                  <StatHintTooltip text={PLATFORM_HINT}>
                    <span aria-label="Как считается">ⓘ</span>
                  </StatHintTooltip>
                </span>
                <div className="lb-gender-tabs" role="group" aria-label="Система">
                  {data.platforms.map((value) => (
                    <button
                      key={value}
                      type="button"
                      aria-pressed={platform === value}
                      className={`lb-gender-tab${platform === value ? " lb-gender-tab-active" : ""}`}
                      onClick={() => setPlatform(value as LocationRecordsPlatform)}
                    >
                      {LOCATION_RECORDS_PLATFORM_LABELS[value] ?? value}
                    </button>
                  ))}
                </div>
              </div>
            )}
          {/* Поиск — на одной линии с последним рядом фильтров (в абсолютном
              зачёте это «Система»), а не отдельной полосой над таблицей: справа
              от фильтров всё равно пустовало пол-экрана. Живёт внутри панели,
              чтобы попадать в её рамку, как на остальных рейтингах. */}
          <div className="lb-controls-right">
            <span className="lb-visits-label">Поиск</span>
            <input
              className="lb-search lb-locrec-search"
              type="search"
              placeholder="Поиск по локации, городу, имени…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          </div>
        </div>

        {loading && !data && <p className="muted">Считаем рейтинг… Первый расчёт может занять до минуты.</p>}
        {error && (
          <div className="lb-error">
            <p>{error}</p>
            <button type="button" className="btn btn-sm" onClick={() => void load()}>
              Повторить
            </button>
          </div>
        )}

        {data && (
          <div className={`lb-page-body${loading ? " lb-refreshing" : ""}`}>
            {rows.length === 0 ? (
              <p className="muted">Ничего не нашлось — попробуйте другой запрос или другую группу.</p>
            ) : narrowViewport ? (
              <div className="rowcards">
                {visibleRows.map((row) => (
                  <div className="rowcard" key={`${row.slug}-${row.place}`}>
                    <div className="rowcard-rank">{row.place}</div>
                    <div className="rowcard-mid">
                      <div className="rowcard-title">
                        <LocationCell row={row} />
                      </div>
                      <div className="rowcard-sub">
                        <RunnerName name={row.runner_name} handle={row.runner_handle} /> ·{" "}
                        <RecordDate row={row} />
                      </div>
                    </div>
                    <div className="rowcard-right">
                      <div className="rowcard-value">{stripLeadingHours(row.finish_time_display)}</div>
                      {data.scope === "absolute" && data.platform === "all" && (
                        <div className="rowcard-sub">
                          {row.platform_code ? (
                            <PlatformBadge code={row.platform_code} />
                          ) : (
                            (row.platform_label ?? "—")
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <TableWrap className="lb-table-wrap lb-table-wrap-flat" innerRef={attachFloatingHead}>
                <table className="data-table lb-table lb-locrec-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Локация</th>
                      <SortableHeader
                        label="Рекорд"
                        sortKey="time"
                        sort={sort}
                        onSort={toggleSort}
                        className="lb-locrec-time"
                      />
                      <th>Рекордсмен</th>
                      <SortableHeader
                        label="Дата"
                        sortKey="date"
                        sort={sort}
                        onSort={toggleSort}
                        className="lb-locrec-date"
                      />
                      {data.scope === "absolute" && data.platform === "all" && <th>Система</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {visibleRows.map((row) => (
                      <tr key={`${row.slug}-${row.place}`}>
                        <td className="lb-locrec-rank">{row.place}</td>
                        <td>
                          <LocationCell row={row} />
                        </td>
                        <td className="lb-locrec-time">{stripLeadingHours(row.finish_time_display)}</td>
                        <td>
                          <RunnerName name={row.runner_name} handle={row.runner_handle} />
                        </td>
                        <td className="lb-locrec-date">
                          <RecordDate row={row} />
                        </td>
                        {data.scope === "absolute" && data.platform === "all" && (
                          <td className="lb-locrec-platform">
                            {row.platform_code ? (
                              <PlatformBadge code={row.platform_code} />
                            ) : (
                              (row.platform_label ?? "—")
                            )}
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}

            {visibleCount < rows.length && (
              <div className="lb-more">
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => setVisibleCount((current) => current + PAGE_STEP)}
                >
                  Показать ещё (места {visibleCount + 1}–{Math.min(visibleCount + PAGE_STEP, rows.length)})
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </PortalSectionShell>
  );
}
