import { useEffect, useMemo, useState } from "react";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { LocationStatusBadge } from "../../components/LocationStatusBadge";
import { PlatformBadge } from "../../components/PlatformBadge";
import { ScrollToTopButton } from "../../components/ScrollToTopButton";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { getLastResults, type LastResultsItem } from "../../lib/api";
import { formatDate, formatFinishTimeValue, formatInt, pluralizeRu } from "../../lib/format";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { TableViewToggle } from "../../components/tableUx/TableViewToggle";
import { useTableColumns } from "../../components/tableUx/useTableColumns";
import type { AdaptiveColumn } from "../../components/tableUx/useAdaptiveColumns";

const PLATFORM_FILTERS = ["five_verst", "s95", "runpark"] as const;

type SortKey =
  | "event_number"
  | "name"
  | "city"
  | "event_date"
  | "finishers"
  | "volunteers"
  | "debutants"
  | "prs"
  | "best_male"
  | "best_female";

// Алфавит и времена логичнее открывать по возрастанию («лучше» = меньше).
const ASC_FIRST_KEYS: SortKey[] = ["name", "city", "best_male", "best_female"];
type SortState = { key: SortKey; asc: boolean };

/** Времена — как в журнале протоколов: без ведущих «00:» часов. */
function formatTime(display: string | null, sec: number | null): string {
  if (sec === null && !display) {
    return "—";
  }
  return formatFinishTimeValue(display, sec).replace(/^00:/, "");
}

function matchesQuery(item: LastResultsItem, query: string): boolean {
  const haystack = [item.name, item.city, item.region, item.country]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(query);
}

function sortValue(item: LastResultsItem, key: SortKey): number | string | null {
  switch (key) {
    case "event_number":
      return item.event_number;
    case "name":
      return item.name.toLowerCase();
    case "city":
      return (item.city ?? "").toLowerCase();
    case "event_date":
      return item.event_date;
    case "finishers":
      return item.finishers;
    case "volunteers":
      return item.volunteers;
    case "debutants":
      return item.debutants;
    case "prs":
      return item.prs;
    case "best_male":
      return item.best_male_time_sec;
    case "best_female":
      return item.best_female_time_sec;
  }
}

// Колонки краткого вида в порядке важности (ширины — как в CSS .loc-index-table).
const LAST_RESULTS_COLUMNS: AdaptiveColumn[] = [
  // Номер старта — первым столбцом (просьба Дмитрия 22.08.2026). Узкий и
  // обязательный: без него строка теряет главную примету «какой это по счёту».
  { key: "event_number", width: 72, required: true },
  { key: "name", width: 170, required: true },
  { key: "event_date", width: 160, required: true },
  { key: "finishers", width: 148, required: true },
  { key: "city", width: 160 },
  { key: "platform", width: 104 },
  { key: "volunteers", width: 148 },
  { key: "debutants", width: 148 },
  { key: "best_male", width: 184 },
  { key: "best_female", width: 184 },
  { key: "prs", width: 184 },
];

function LastResultsTable({ items }: { items: LastResultsItem[] }) {
  const [sort, setSort] = useState<SortState>({ key: "event_date", asc: false });
  // Краткий вид набирает колонки по ширине экрана: минимум — локация, дата и
  // финишёры, дальше город, система, волонтёры и так до полного набора.
  const tableColumns = useTableColumns(LAST_RESULTS_COLUMNS);
  const showFull = tableColumns.showFull;
  const show = tableColumns.show;

  const sorted = useMemo(() => {
    const copy = [...items];
    copy.sort((a, b) => {
      const left = sortValue(a, sort.key);
      const right = sortValue(b, sort.key);
      if (left === right) {
        // При равенстве ключа — свежие и многолюдные выше (порядок бэкенда).
        if (a.event_date !== b.event_date) {
          return a.event_date < b.event_date ? 1 : -1;
        }
        return (b.finishers ?? 0) - (a.finishers ?? 0);
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

  const visibleCount = LAST_RESULTS_COLUMNS.filter((column) => show(column.key)).length;

  return (
    <>
      <TableViewToggle columns={tableColumns} />
      <TableWrap stickyFirstCol={showFull} outerRef={tableColumns.measureRef}>
        <table
          className={`data-table data-table-layout-fixed loc-index-table last-results-table${
            showFull ? "" : " data-table-short"
          }`}
          style={showFull ? undefined : { minWidth: tableColumns.minWidth }}
        >
          <colgroup>
            <col className="col-compact" />
            <col />
            {show("city") && <col className="col-city" />}
            {show("platform") && <col className="col-platform" />}
            <col className="col-date" />
            <col className="col-metric" />
            {show("volunteers") && <col className="col-metric" />}
            {show("debutants") && <col className="col-metric" />}
            {show("prs") && <col className="col-metric-wide" />}
            {show("best_male") && <col className="col-metric-wide" />}
            {show("best_female") && <col className="col-metric-wide" />}
          </colgroup>
          <thead>
            <tr>
              <ColumnHeader
                label="№"
                headerTitle="Номер старта у локации внутри её системы (сквозного номера по всем системам здесь нет)"
                {...sortProps("event_number")}
              />
              <ColumnHeader label="Локация" {...sortProps("name")} />
              {show("city") && <ColumnHeader label="Город" {...sortProps("city")} />}
              {show("platform") && <ColumnHeader label="Система" filterable={false} />}
              <ColumnHeader
                label="Дата"
                hint="Дата последнего старта локации (клик по дате — протокол)"
                {...sortProps("event_date")}
              />
              <ColumnHeader
                label="Финишёров"
                hint="Финишёров на последнем старте"
                {...sortProps("finishers")}
              />
              {show("volunteers") && (
                <ColumnHeader
                  label="Волонтёров"
                  hint="Волонтёров на последнем старте"
                  {...sortProps("volunteers")}
                />
              )}
              {show("debutants") && (
                <ColumnHeader
                  label="Дебютантов"
                  hint="Дебютантов: впервые вышли на субботний старт"
                  {...sortProps("debutants")}
                />
              )}
              {show("prs") && (
                <ColumnHeader
                  label="Личных рекордов"
                  hint="Личных рекордов на этом старте"
                  {...sortProps("prs")}
                />
              )}
              {show("best_male") && (
                <ColumnHeader
                  label="Лучшее время (М)"
                  hint="Лучшее мужское время последнего старта"
                  {...sortProps("best_male")}
                />
              )}
              {show("best_female") && (
                <ColumnHeader
                  label="Лучшее время (Ж)"
                  hint="Лучшее женское время последнего старта"
                  {...sortProps("best_female")}
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
                  <td className="td-compact">
                    {item.event_number != null ? formatInt(item.event_number) : "—"}
                  </td>
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
                  {show("platform") && (
                    <td>
                      <span className="loc-index-platforms">
                        {item.event_platform_codes.map((code) => (
                          <PlatformBadge key={code} code={code} />
                        ))}
                      </span>
                    </td>
                  )}
                  <td className={item.is_last_saturday ? undefined : "muted"}>
                    {/* Полный протокол есть у нас — дата ведёт на нашу страницу
                        протокола; иначе остаётся внешняя ссылка платформы. */}
                    {item.has_protocol && item.event_platform_code ? (
                      <a
                        href={`/locations/${encodeURIComponent(item.slug)}/protocol/${item.event_platform_code}/${item.event_date}`}
                        title="Открыть протокол старта"
                      >
                        {formatDate(item.event_date)}
                      </a>
                    ) : item.protocol_url ? (
                      <a href={item.protocol_url} target="_blank" rel="noreferrer" title="Открыть протокол">
                        {formatDate(item.event_date)}
                      </a>
                    ) : (
                      formatDate(item.event_date)
                    )}
                  </td>
                  <td className="td-compact">{item.finishers != null ? formatInt(item.finishers) : "—"}</td>
                  {show("volunteers") && <td className="td-compact">{item.volunteers != null ? formatInt(item.volunteers) : "—"}</td>}
                  {show("debutants") && <td className="td-compact">{item.debutants != null ? formatInt(item.debutants) : "—"}</td>}
                  {show("prs") && <td className="td-compact">{item.prs != null ? formatInt(item.prs) : "—"}</td>}
                  {show("best_male") && (
                    <td className="td-compact">
                      {formatTime(item.best_male_time_display, item.best_male_time_sec)}
                    </td>
                  )}
                  {show("best_female") && (
                    <td className="td-compact">
                      {formatTime(item.best_female_time_display, item.best_female_time_sec)}
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

function LastResultsContent() {
  const [items, setItems] = useState<LastResultsItem[] | null>(null);
  const [saturdayDate, setSaturdayDate] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [platformFilter, setPlatformFilter] = useState<string | null>(null);
  const [showPaused, setShowPaused] = useState(false);
  const [onlySaturday, setOnlySaturday] = useState(false);

  useEffect(() => {
    getLastResults()
      .then((data) => {
        setItems(data.items);
        setSaturdayDate(data.saturday_date);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить результаты"));
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
      if (platformFilter && !item.event_platform_codes.includes(platformFilter)) {
        return false;
      }
      if (!showPaused && (item.is_paused || item.is_cancelled)) {
        return false;
      }
      if (onlySaturday && !item.is_last_saturday) {
        return false;
      }
      return true;
    });
  }, [items, query, platformFilter, showPaused, onlySaturday]);

  const saturdayCount = useMemo(
    () => (items ? items.filter((item) => item.is_last_saturday).length : 0),
    [items],
  );

  return (
    <PortalSectionShell sidebar={{ active: "last-results" }}>
      <header className="loc-header">
        <div className="loc-header-title">
          <h1>Результаты последней субботы</h1>
        </div>
        <p className="muted loc-header-place">
          Последний старт каждой площадки 5 вёрст, С95 и RunPark: финишёры, волонтёры,
          лучшие времена и новички.
          {saturdayDate &&
            ` В субботу ${formatDate(saturdayDate)} стартовало ${pluralizeRu(saturdayCount, [
              "локация",
              "локации",
              "локаций",
            ])}.`}
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
                checked={onlySaturday}
                onChange={(event) => setOnlySaturday(event.target.checked)}
              />{" "}
              только последняя суббота
            </label>
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
            <LastResultsTable items={filtered} />
          </>
        )}
      </section>
      <ScrollToTopButton />
    </PortalSectionShell>
  );
}

// Страница открыта без логина: посадочная под запросы «5 вёрст результаты».
export function LastResultsPage() {
  return <LastResultsContent />;
}
