import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { LocationStatusBadge } from "../../components/LocationStatusBadge";
import { PlatformBadge } from "../../components/PlatformBadge";
import { RequireAdmin } from "../../components/RequireAdmin";
import { SiteHeader } from "../../components/SiteHeader";
import { getLocationsIndex, logout, type LocationIndexItem, type User } from "../../lib/api";
import { formatDate, pluralizeRu } from "../../lib/format";
import { SITE_HOME_HREF } from "../../lib/siteBrand";
import { APP_NAV_ITEMS } from "../../lib/siteNav";

const PLATFORM_FILTERS = ["five_verst", "s95", "runpark"] as const;

type SortKey = "name" | "city" | "events_count" | "finishers_total" | "first_event_date";
type SortState = { key: SortKey; asc: boolean };

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
      current.key === key ? { key, asc: !current.asc } : { key, asc: key === "name" || key === "city" },
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
              label="Первый старт"
              {...sortProps("first_event_date")}
            />
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 ? (
            <tr>
              <td colSpan={6} className="table-empty-cell">
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

      <section className="card loc-section">
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
    </IndexShell>
  );
}

export function LocationsIndexPage() {
  return <RequireAdmin>{(user) => <LocationsIndexContent currentUser={user} />}</RequireAdmin>;
}
