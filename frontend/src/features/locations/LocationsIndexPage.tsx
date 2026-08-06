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

function LocationsTable({ items }: { items: LocationIndexItem[] }) {
  const [sort, setSort] = useState<SortState>({ key: "events_count", asc: false });
  // «Кратко | Полно» действует только на узких экранах; десктоп всегда полный.
  const [tableView, setTableView] = useTableView("locationsIndex");
  // Краткий вид доступен и на компьютере (решение Дмитрия 04.08.2026): в полном
  // наборе колонок названиям локаций достаётся слишком мало места и они режутся
  // многоточием, а лишние столбцы нужны не всегда.
  const showFull = tableView === "full";

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
          <col className="col-platform" />
          <col className="col-metric" />
          {showFull && (
            <>
              <col className="col-metric" />
              <col className="col-metric-wide" />
              <col className="col-metric-wide" />
              <col className="col-metric" />
              <col className="col-metric" />
              <col className="col-date" />
            </>
          )}
        </colgroup>
        <thead>
          <tr>
            <ColumnHeader label="Локация" {...sortProps("name")} />
            {showFull && <ColumnHeader label="Город" {...sortProps("city")} />}
            <ColumnHeader label="Системы" filterable={false} />
            <ColumnHeader
              label="Стартов"
              hint="Мероприятий проведено за всё время"
              {...sortProps("events_count")}
            />
            {showFull && (
              <ColumnHeader
                label="Финишей"
                hint="Финишей за всё время"
                {...sortProps("finishers_total")}
              />
            )}
            {showFull && (
              <ColumnHeader
                label="В среднем"
                hint="Средняя явка: финишей за всё время ÷ число стартов"
                {...sortProps("avg_finishers")}
              />
            )}
            {showFull && (
              <ColumnHeader
                label="Рекорд явки"
                hint="Максимум финишей за один старт (в подсказке — дата)"
                {...sortProps("attendance_record")}
              />
            )}
            {showFull && (
              <ColumnHeader
                label="Луч. М"
                hint="Рекорд локации (LR): лучшее время мужчины здесь за всю историю"
                {...sortProps("best_male")}
              />
            )}
            {showFull && (
              <ColumnHeader
                label="Луч. Ж"
                hint="Рекорд локации (LR): лучшее время женщины здесь за всю историю"
                {...sortProps("best_female")}
              />
            )}
            {showFull && (
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
                <td>
                  <span className="loc-index-platforms">
                    {item.platform_codes.map((code) => (
                      <PlatformBadge key={code} code={code} />
                    ))}
                  </span>
                </td>
                <td className="td-compact">{item.events_count || "—"}</td>
                {showFull && <td className="td-compact">{item.finishers_total || "—"}</td>}
                {showFull && <td className="td-compact">{formatAvgFinishers(item)}</td>}
                {showFull && (
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
                {showFull && (
                  <td className="td-compact">
                    {formatRecord(item.best_male_time_display, item.best_male_time_sec)}
                  </td>
                )}
                {showFull && (
                  <td className="td-compact">
                    {formatRecord(item.best_female_time_display, item.best_female_time_sec)}
                  </td>
                )}
                {showFull && <td>{item.first_event_date ? formatDate(item.first_event_date) : "—"}</td>}
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
