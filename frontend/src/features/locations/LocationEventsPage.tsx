import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ActivityDateLink } from "../../components/ActivityDateLink";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { PlatformBadge } from "../../components/PlatformBadge";
import { RequireAuth } from "../../components/RequireAuth";
import { SiteHeader } from "../../components/SiteHeader";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import {
  ApiError,
  getLocationEvents,
  logout,
  type LocationEventRow,
  type LocationEvents,
  type User,
} from "../../lib/api";
import { platformCodeLabel } from "../../lib/format";
import { SITE_HOME_HREF } from "../../lib/siteBrand";
import { APP_NAV_ITEMS } from "../../lib/siteNav";

type SortKey = "date" | "finishers" | "volunteers" | "best_male" | "best_female" | "avg" | "newcomers" | "prs";

type SortState = { key: SortKey; asc: boolean };

function rowNewcomers(row: LocationEventRow): number | null {
  if (row.debutants === null && row.first_at_location === null) {
    return null;
  }
  return (row.debutants ?? 0) + (row.first_at_location ?? 0);
}

function sortValue(row: LocationEventRow, key: SortKey): number | string | null {
  switch (key) {
    case "date":
      return row.event_date;
    case "finishers":
      return row.finishers;
    case "volunteers":
      return row.volunteers;
    case "best_male":
      return row.best_male_time_sec;
    case "best_female":
      return row.best_female_time_sec;
    case "avg":
      return row.avg_time_sec;
    case "newcomers":
      return rowNewcomers(row);
    case "prs":
      return row.prs;
  }
}

function EventsShell({ children, currentUser }: { children: ReactNode; currentUser: User }) {
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

function LocationEventsContent({ slug, currentUser }: { slug: string; currentUser: User }) {
  const [data, setData] = useState<LocationEvents | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [platformFilter, setPlatformFilter] = useState<string | null>(null);
  const [sort, setSort] = useState<SortState>({ key: "date", asc: false });

  useEffect(() => {
    let cancelled = false;
    getLocationEvents(slug)
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
        }
      })
      .catch((err) => {
        if (cancelled) {
          return;
        }
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError(err instanceof Error ? err.message : "Не удалось загрузить журнал");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  const platformCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of data?.items ?? []) {
      counts.set(item.platform_code, (counts.get(item.platform_code) ?? 0) + 1);
    }
    return counts;
  }, [data]);

  const rows = useMemo(() => {
    if (!data) {
      return [];
    }
    const filtered = platformFilter
      ? data.items.filter((item) => item.platform_code === platformFilter)
      : [...data.items];
    filtered.sort((a, b) => {
      const left = sortValue(a, sort.key);
      const right = sortValue(b, sort.key);
      if (left === right) {
        return a.event_date < b.event_date ? 1 : -1;
      }
      // null-значения всегда в конец, независимо от направления.
      if (left === null) {
        return 1;
      }
      if (right === null) {
        return -1;
      }
      const compare = left < right ? -1 : 1;
      return sort.asc ? compare : -compare;
    });
    return filtered;
  }, [data, platformFilter, sort]);

  const toggleSort = (key: SortKey) => {
    setSort((current) =>
      current.key === key ? { key, asc: !current.asc } : { key, asc: key === "date" ? false : true },
    );
  };

  const sortProps = (key: SortKey) => ({
    filterable: false,
    sortActive: sort.key === key,
    sortAsc: sort.asc,
    onSort: () => toggleSort(key),
  });

  if (notFound) {
    return (
      <EventsShell currentUser={currentUser}>
        <div className="card">
          <p className="muted">Локация не найдена.</p>
          <p>
            <a href="/locations">Все локации</a>
          </p>
        </div>
      </EventsShell>
    );
  }

  if (error) {
    return (
      <EventsShell currentUser={currentUser}>
        <div className="card error">
          <p>{error}</p>
        </div>
      </EventsShell>
    );
  }

  if (!data) {
    return (
      <EventsShell currentUser={currentUser}>
        <p className="muted">Загрузка…</p>
      </EventsShell>
    );
  }

  return (
    <EventsShell currentUser={currentUser}>
      <header className="loc-header">
        <p className="muted loc-header-breadcrumb">
          <a href="/locations">← Все локации</a> /{" "}
          <a href={`/locations/${data.slug}`}>{data.name}</a> / Журнал
        </p>
        <div className="loc-header-title">
          <h1>{data.name} — журнал протоколов</h1>
        </div>
      </header>

      <div className="map-mode-tabs" role="tablist" aria-label="Фильтр по системам">
        <button
          type="button"
          role="tab"
          aria-selected={platformFilter === null}
          className={platformFilter === null ? "map-mode-tab active" : "map-mode-tab"}
          onClick={() => setPlatformFilter(null)}
        >
          Все ({data.items.length})
        </button>
        {[...platformCounts.entries()].map(([code, count]) => (
          <button
            key={code}
            type="button"
            role="tab"
            aria-selected={platformFilter === code}
            className={platformFilter === code ? "map-mode-tab active" : "map-mode-tab"}
            onClick={() => setPlatformFilter(platformFilter === code ? null : code)}
          >
            {platformCodeLabel(code)} ({count})
          </button>
        ))}
      </div>

      <section className="loc-section">
        <div className="table-wrap">
          <table className="data-table data-table-layout-fixed loc-events-table">
            <colgroup>
              <col className="col-number" />
              <col className="col-date" />
              <col className="col-platform" />
              <col className="col-compact" />
              <col className="col-compact" />
              <col className="col-compact" />
              <col className="col-time" />
              <col className="col-time" />
              <col className="col-time" />
              <col className="col-compact" />
            </colgroup>
            <thead>
              <tr>
                <ColumnHeader
                  label="№"
                  headerTitle="Номер события в системе; в скобках — сквозной номер сбора локации по всем системам"
                  filterable={false}
                />
                <ColumnHeader label="Дата" {...sortProps("date")} />
                <ColumnHeader label="Система" filterable={false} />
                <ColumnHeader
                  label="Фин."
                  headerTitle="Финишёров на старте"
                  {...sortProps("finishers")}
                />
                <ColumnHeader
                  label="Вол."
                  headerTitle="Волонтёров на старте"
                  {...sortProps("volunteers")}
                />
                <ColumnHeader
                  label="Нов."
                  headerTitle="Новички: дебютанты движения + впервые на этой локации"
                  {...sortProps("newcomers")}
                />
                <ColumnHeader
                  label="Луч. М"
                  headerTitle="Лучшее время дня среди мужчин"
                  {...sortProps("best_male")}
                />
                <ColumnHeader
                  label="Луч. Ж"
                  headerTitle="Лучшее время дня среди женщин"
                  {...sortProps("best_female")}
                />
                <ColumnHeader
                  label="Средн."
                  headerTitle="Среднее время финиша"
                  {...sortProps("avg")}
                />
                <ColumnHeader
                  label="PR"
                  headerTitle="Личных рекордов установлено в этот день"
                  {...sortProps("prs")}
                />
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={10} className="table-empty-cell">
                    <span className="muted">Нет стартов по выбранному фильтру</span>
                  </td>
                </tr>
              ) : (
                rows.map((row) => (
                  <tr key={`${row.platform_code}-${row.event_date}`}>
                    <td className="td-compact">
                      <span className="loc-events-number">
                        {row.event_number ?? "—"}
                        <StatHintTooltip text="Сквозной номер старта — какой это по счёту сбор локации за всю историю, по всем системам вместе">
                          <span className="muted">({row.overall_number})</span>
                        </StatHintTooltip>
                        {row.is_attendance_record && (
                          <RecordIcon
                            icon="🏆"
                            ariaLabel="Рекорд посещаемости"
                            tooltip="Новый рекорд посещаемости локации на момент этого старта"
                          />
                        )}
                      </span>
                    </td>
                    <td className="td-date">
                      <ActivityDateLink date={row.event_date} url={row.protocol_url} />
                    </td>
                    <td className="td-platform">
                      <PlatformBadge code={row.platform_code} />
                    </td>
                    <td className="td-compact">{row.finishers ?? "—"}</td>
                    <td className="td-compact">{row.volunteers ?? "—"}</td>
                    <td className="td-compact">{rowNewcomers(row) ?? "—"}</td>
                    <td className="td-time">
                      <span className="loc-events-number">
                        {stripHours(row.best_male_time_display)}
                        {row.is_course_record_male && (
                          <RecordIcon
                            icon="🏆"
                            ariaLabel="Рекорд трассы, мужчины"
                            tooltip="Новый рекорд трассы среди мужчин на момент этого старта"
                          />
                        )}
                      </span>
                    </td>
                    <td className="td-time">
                      <span className="loc-events-number">
                        {stripHours(row.best_female_time_display)}
                        {row.is_course_record_female && (
                          <RecordIcon
                            icon="🏆"
                            ariaLabel="Рекорд трассы, женщины"
                            tooltip="Новый рекорд трассы среди женщин на момент этого старта"
                          />
                        )}
                      </span>
                    </td>
                    <td className="td-time">{stripHours(row.avg_time_display)}</td>
                    <td className="td-compact">{row.prs ?? "—"}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        {rows.some((row) => !row.has_protocol) && (
          <p className="table-foot muted">
            У части стартов нет полного протокола — по ним показаны только сводные цифры (обычно это события
            parkrun-эпохи)
          </p>
        )}
      </section>
    </EventsShell>
  );
}

export function LocationEventsPage({ slug }: { slug: string }) {
  return <RequireAuth>{(user) => <LocationEventsContent slug={slug} currentUser={user} />}</RequireAuth>;
}

function RecordIcon({ icon, ariaLabel, tooltip }: { icon: string; ariaLabel: string; tooltip: string }) {
  return (
    <StatHintTooltip text={tooltip}>
      <span className="loc-events-record-icon" aria-label={ariaLabel}>
        {icon}
      </span>
    </StatHintTooltip>
  );
}

/** «00:18:08» → «18:08» (часовые времена на 5 км не встречаются). */
function stripHours(display: string | null): string {
  if (!display) {
    return "—";
  }
  return display.replace(/^00:/, "");
}
