import { useEffect, useMemo, useState } from "react";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { LocationStatusBadge } from "../../components/LocationStatusBadge";
import { PlatformBadge } from "../../components/PlatformBadge";
import { ScrollToTopButton } from "../../components/ScrollToTopButton";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { getLastResults, type LastResultsItem } from "../../lib/api";
import { formatDate, formatFinishTimeValue, pluralizeRu } from "../../lib/format";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { TableViewToggle, useTableView } from "../../components/tableUx/TableViewToggle";

const PLATFORM_FILTERS = ["five_verst", "s95", "runpark"] as const;

type SortKey =
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

function LastResultsTable({ items }: { items: LastResultsItem[] }) {
  const [sort, setSort] = useState<SortState>({ key: "event_date", asc: false });
  const [tableView, setTableView] = useTableView("lastResults");
  const showFull = tableView === "full";

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

  return (
    <>
      <TableViewToggle
        value={tableView}
        onChange={setTableView}
        className="tview-toggle-always"
      />
      <TableWrap stickyFirstCol={showFull}>
        <table
          className={`data-table data-table-layout-fixed loc-index-table${
            showFull ? "" : " data-table-short"
          }`}
        >
          <colgroup>
            <col />
            {showFull && <col className="col-city" />}
            {showFull && <col className="col-platform" />}
            <col className="col-date" />
            <col className="col-metric" />
            {showFull && (
              <>
                <col className="col-metric" />
                <col className="col-metric" />
                <col className="col-metric" />
                <col className="col-metric" />
                <col className="col-metric" />
              </>
            )}
          </colgroup>
          <thead>
            <tr>
              <ColumnHeader label="Локация" {...sortProps("name")} />
              {showFull && <ColumnHeader label="Город" {...sortProps("city")} />}
              {showFull && <ColumnHeader label="Система" filterable={false} />}
              <ColumnHeader
                label="Дата"
                hint="Дата последнего старта локации (клик по дате — протокол)"
                {...sortProps("event_date")}
              />
              <ColumnHeader
                label="Фин."
                hint="Финишёров на последнем старте"
                {...sortProps("finishers")}
              />
              {showFull && (
                <ColumnHeader
                  label="Вол."
                  hint="Волонтёров на последнем старте"
                  {...sortProps("volunteers")}
                />
              )}
              {showFull && (
                <ColumnHeader
                  label="Нов."
                  hint="Дебютантов: впервые вышли на субботний старт"
                  {...sortProps("debutants")}
                />
              )}
              {showFull && (
                <ColumnHeader
                  label="PR"
                  hint="Личных рекордов на этом старте"
                  {...sortProps("prs")}
                />
              )}
              {showFull && (
                <ColumnHeader
                  label="Луч. М"
                  hint="Лучшее мужское время последнего старта"
                  {...sortProps("best_male")}
                />
              )}
              {showFull && (
                <ColumnHeader
                  label="Луч. Ж"
                  hint="Лучшее женское время последнего старта"
                  {...sortProps("best_female")}
                />
              )}
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? (
              <tr>
                <td colSpan={showFull ? 10 : 3} className="table-empty-cell">
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
                  {showFull && (
                    <td className="muted">
                      {item.city ?? "—"}
                      {item.country && item.country !== "Россия" ? ` · ${item.country}` : ""}
                    </td>
                  )}
                  {showFull && (
                    <td>
                      <span className="loc-index-platforms">
                        {item.event_platform_codes.map((code) => (
                          <PlatformBadge key={code} code={code} />
                        ))}
                      </span>
                    </td>
                  )}
                  <td className={item.is_last_saturday ? undefined : "muted"}>
                    {item.protocol_url ? (
                      <a href={item.protocol_url} target="_blank" rel="noreferrer" title="Открыть протокол">
                        {formatDate(item.event_date)}
                      </a>
                    ) : (
                      formatDate(item.event_date)
                    )}
                  </td>
                  <td className="td-compact">{item.finishers ?? "—"}</td>
                  {showFull && <td className="td-compact">{item.volunteers ?? "—"}</td>}
                  {showFull && <td className="td-compact">{item.debutants ?? "—"}</td>}
                  {showFull && <td className="td-compact">{item.prs ?? "—"}</td>}
                  {showFull && (
                    <td className="td-compact">
                      {formatTime(item.best_male_time_display, item.best_male_time_sec)}
                    </td>
                  )}
                  {showFull && (
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
