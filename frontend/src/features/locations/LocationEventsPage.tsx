import { useEffect, useMemo, useState } from "react";
import { ActivityDateLink } from "../../components/ActivityDateLink";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { PlatformBadge } from "../../components/PlatformBadge";
import { ScrollToTopButton } from "../../components/ScrollToTopButton";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import {
  ApiError,
  getLocationEvents,
  type LocationEventRow,
  type LocationEvents,
} from "../../lib/api";
import { platformCodeLabel } from "../../lib/format";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { TableViewToggle, useTableView } from "../../components/tableUx/TableViewToggle";
import { useNarrowViewport } from "../../components/tableUx/useNarrowViewport";
import { PortalSectionShell } from "../portal/PortalSectionShell";

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

function LocationEventsContent({ slug }: { slug: string }) {
  const [data, setData] = useState<LocationEvents | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [platformFilter, setPlatformFilter] = useState<string | null>(null);
  const [sort, setSort] = useState<SortState>({ key: "date", asc: false });
  const attachFloatingHead = useFloatingTableHead();
  // «Кратко | Полно» действует только на узких экранах; десктоп всегда полный.
  const [tableView, setTableView] = useTableView("locationEvents");
  const narrowViewport = useNarrowViewport();
  const showFull = !narrowViewport || tableView === "full";

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
      <PortalSectionShell sidebar={{ active: "locations" }}>
        <div className="card">
          <p className="muted">Локация не найдена.</p>
          <p>
            <a href="/locations">Все локации</a>
          </p>
        </div>
      </PortalSectionShell>
    );
  }

  if (error) {
    return (
      <PortalSectionShell sidebar={{ active: "locations" }}>
        <div className="card error">
          <p>{error}</p>
        </div>
      </PortalSectionShell>
    );
  }

  if (!data) {
    return (
      <PortalSectionShell sidebar={{ active: "locations" }}>
        <p className="muted">Загрузка…</p>
      </PortalSectionShell>
    );
  }

  return (
    <PortalSectionShell sidebar={{ active: "locations", location: { slug: data.slug, name: data.name } }}>
      <header className="loc-header loc-wide-page">
        <p className="muted loc-header-breadcrumb">
          <a href="/locations">← Все локации</a> /{" "}
          <a href={`/locations/${data.slug}`}>{data.name}</a> / Журнал
        </p>
        <div className="loc-header-title">
          <h1>{data.name} — журнал протоколов</h1>
        </div>
      </header>

      {/* У большинства локаций протоколы одной системы — фильтровать нечего,
          и ряд из «Все (N)» и единственной кнопки только утяжеляет страницу. */}
      {platformCounts.size > 1 && (
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
      )}

      <section className="loc-section">
        <TableViewToggle value={tableView} onChange={setTableView} />
        <TableWrap innerRef={attachFloatingHead} className="loc-events-wrap" stickyFirstCol={showFull}>
          <table
            className={`data-table data-table-layout-fixed loc-events-table${
              showFull ? "" : " data-table-short"
            }`}
          >
            <colgroup>
              <col className="col-number" />
              <col className="col-date" />
              <col className="col-platform" />
              <col className="col-compact" />
              {showFull && (
                <>
                  <col className="col-compact" />
                  <col className="col-compact" />
                </>
              )}
              <col className="col-time" />
              <col className="col-time" />
              {showFull && (
                <>
                  <col className="col-time" />
                  <col className="col-compact" />
                </>
              )}
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
                  hint="Финишёров на старте"
                  {...sortProps("finishers")}
                />
                {showFull && (
                  <ColumnHeader
                    label="Вол."
                    hint="Волонтёров на старте"
                    {...sortProps("volunteers")}
                  />
                )}
                {showFull && (
                  <ColumnHeader
                    label="Нов."
                    hint="Новички: дебютанты движения + впервые на этой локации"
                    {...sortProps("newcomers")}
                  />
                )}
                <ColumnHeader
                  label="Луч. М"
                  hint="Лучшее время среди мужчин"
                  {...sortProps("best_male")}
                />
                <ColumnHeader
                  label="Луч. Ж"
                  hint="Лучшее время среди женщин"
                  {...sortProps("best_female")}
                />
                {showFull && (
                  <ColumnHeader
                    label="Средн."
                    hint="Среднее время финиша"
                    {...sortProps("avg")}
                  />
                )}
                {showFull && (
                  <ColumnHeader
                    label="PR"
                    hint="Личных рекордов установлено в этот день"
                    {...sortProps("prs")}
                  />
                )}
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={showFull ? 10 : 6} className="table-empty-cell">
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
                    {showFull && <td className="td-compact">{row.volunteers ?? "—"}</td>}
                    {showFull && <td className="td-compact">{rowNewcomers(row) ?? "—"}</td>}
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
                    {showFull && <td className="td-time">{stripHours(row.avg_time_display)}</td>}
                    {showFull && <td className="td-compact">{row.prs ?? "—"}</td>}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </TableWrap>
        {rows.some((row) => !row.has_protocol) && (
          <p className="table-foot muted">
            У части стартов нет полного протокола — по ним показаны только сводные цифры (обычно это события
            parkrun-эпохи)
          </p>
        )}
      </section>
      <ScrollToTopButton />
    </PortalSectionShell>
  );
}

// Журнал открыт без логина, как и страница локации.
export function LocationEventsPage({ slug }: { slug: string }) {
  return <LocationEventsContent slug={slug} />;
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
