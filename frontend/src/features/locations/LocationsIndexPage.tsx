import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { LocationStatusBadge } from "../../components/LocationStatusBadge";
import { PlatformBadge } from "../../components/PlatformBadge";
import { RequireAuth } from "../../components/RequireAuth";
import { ScrollToTopButton } from "../../components/ScrollToTopButton";
import { SiteHeader } from "../../components/SiteHeader";
import { getLocationsIndex, logout, type LocationIndexItem, type User } from "../../lib/api";
import { formatDate, formatFinishTimeValue, pluralizeRu } from "../../lib/format";
import { SITE_HOME_HREF } from "../../lib/siteBrand";
import { APP_NAV_ITEMS } from "../../lib/siteNav";

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

function IndexShell({ children, currentUser }: { children: ReactNode; currentUser: User }) {
  const handleLogout = async () => {
    await logout();
    window.location.href = "/";
  };

  return (
    <div className="shell">
      <SiteHeader
        homeHref={SITE_HOME_HREF}
        navItems={APP_NAV_ITEMS}
        activePath="/locations"
        showAdminNav={currentUser.is_admin}
        actions={
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => void handleLogout()}>
            Выйти
          </button>
        }
      />
      <div className="shell-content">
        <main className="shell-main">{children}</main>
      </div>
    </div>
  );
}

function LocationsTable({ items }: { items: LocationIndexItem[] }) {
  const [sort, setSort] = useState<SortState>({ key: "events_count", asc: false });

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
    <div className="table-wrap">
      <table className="data-table data-table-layout-fixed loc-index-table">
        <colgroup>
          <col />
          <col className="col-city" />
          <col className="col-platform" />
          <col className="col-metric" />
          <col className="col-metric" />
          <col className="col-metric-wide" />
          <col className="col-metric-wide" />
          <col className="col-metric" />
          <col className="col-metric" />
          <col className="col-date" />
        </colgroup>
        <thead>
          <tr>
            <ColumnHeader label="Локация" {...sortProps("name")} />
            <ColumnHeader label="Город" {...sortProps("city")} />
            <ColumnHeader label="Системы" filterable={false} />
            <ColumnHeader
              label="Стартов"
              headerTitle="Мероприятий проведено за всё время"
              {...sortProps("events_count")}
            />
            <ColumnHeader
              label="Финишей"
              headerTitle="Финишей за всё время"
              {...sortProps("finishers_total")}
            />
            <ColumnHeader
              label="В среднем"
              headerTitle="Средняя явка: финишей за всё время ÷ число стартов"
              {...sortProps("avg_finishers")}
            />
            <ColumnHeader
              label="Рекорд явки"
              headerTitle="Максимум финишей за один старт (в подсказке — дата)"
              {...sortProps("attendance_record")}
            />
            <ColumnHeader
              label="Луч. М"
              headerTitle="Рекорд локации (LR): лучшее время мужчины здесь за всю историю"
              {...sortProps("best_male")}
            />
            <ColumnHeader
              label="Луч. Ж"
              headerTitle="Рекорд локации (LR): лучшее время женщины здесь за всю историю"
              {...sortProps("best_female")}
            />
            <ColumnHeader
              label="Первый старт"
              {...sortProps("first_event_date")}
            />
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 ? (
            <tr>
              <td colSpan={10} className="table-empty-cell">
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
                <td className="muted">
                  {item.city ?? "—"}
                  {item.country && item.country !== "Россия" ? ` · ${item.country}` : ""}
                </td>
                <td>
                  <span className="loc-index-platforms">
                    {item.platform_codes.map((code) => (
                      <PlatformBadge key={code} code={code} />
                    ))}
                  </span>
                </td>
                <td className="td-compact">{item.events_count || "—"}</td>
                <td className="td-compact">{item.finishers_total || "—"}</td>
                <td className="td-compact">{formatAvgFinishers(item)}</td>
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
                <td className="td-compact">{formatRecord(item.best_male_time_display, item.best_male_time_sec)}</td>
                <td className="td-compact">
                  {formatRecord(item.best_female_time_display, item.best_female_time_sec)}
                </td>
                <td>{item.first_event_date ? formatDate(item.first_event_date) : "—"}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

function LocationsIndexContent({ currentUser }: { currentUser: User }) {
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
    <IndexShell currentUser={currentUser}>
      <header className="loc-header">
        <div className="loc-header-title">
          <h1>Локации</h1>
        </div>
        <p className="muted loc-header-place">
          Все площадки субботних стартов: цифры, рекорды и история каждой локации.
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
    </IndexShell>
  );
}

export function LocationsIndexPage() {
  return <RequireAuth>{(user) => <LocationsIndexContent currentUser={user} />}</RequireAuth>;
}
