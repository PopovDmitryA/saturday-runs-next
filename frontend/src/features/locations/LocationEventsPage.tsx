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
import { applyPageMeta, locationPageMeta } from "../../lib/pageMeta";
import { flushMetrikaHit } from "../../lib/metrika";
import { formatDate, formatInt, platformCodeLabel } from "../../lib/format";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { TableViewToggle } from "../../components/tableUx/TableViewToggle";
import { useTableColumns } from "../../components/tableUx/useTableColumns";
import type { AdaptiveColumn } from "../../components/tableUx/useAdaptiveColumns";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { locationHintFor, rememberLocationHint } from "../../lib/locationHint";
import {
  FilterGroup,
  FilterPanel,
  FilterRow,
} from "../../components/filters/FilterPanel";
import { LocationAttendanceJournal } from "../journal/LocationAttendanceJournal";

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

// Колонки журнала в порядке важности; ширины — из CSS .loc-events-table
// (col-compact 9.25rem, col-time 11.5rem, col-compact-wide 11.5rem).
const EVENTS_COLUMNS: AdaptiveColumn[] = [
  { key: "number", width: 104, required: true },
  { key: "date", width: 112, required: true },
  { key: "finishers", width: 148, required: true },
  { key: "platform", width: 104 },
  { key: "best_male", width: 184 },
  { key: "best_female", width: 184 },
  { key: "volunteers", width: 148 },
  { key: "newcomers", width: 148 },
  { key: "avg", width: 184 },
  { key: "prs", width: 184 },
];

function LocationEventsContent({ slug }: { slug: string }) {
  const [data, setData] = useState<LocationEvents | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [platformFilter, setPlatformFilter] = useState<string | null>(null);
  const [sort, setSort] = useState<SortState>({ key: "date", asc: false });
  // Второй вид журнала — посещаемость: те же старты, но не сводкой по
  // событиям, а матрицей «участник × даты» (перенос дашборда Grafana).
  // Стартовое значение из ссылки, чтобы видом можно было делиться.
  const [view, setView] = useState<"protocols" | "attendance">(() =>
    new URLSearchParams(window.location.search).get("view") === "attendance"
      ? "attendance"
      : "protocols",
  );
  const switchView = (next: "protocols" | "attendance") => {
    setView(next);
    const url = new URL(window.location.href);
    if (next === "attendance") {
      url.searchParams.set("view", "attendance");
    } else {
      url.searchParams.delete("view");
    }
    window.history.replaceState(null, "", url.toString());
  };
  // Копия шапки встаёт под липкую полосу «Кратко | Полно», а не под шапку сайта.
  const attachFloatingHead = useFloatingTableHead(".tview-bar");
  // Краткий вид набирает колонки под ширину: минимум — номер, дата и финишёры.
  const tableColumns = useTableColumns(EVENTS_COLUMNS);
  const showFull = tableColumns.showFull;
  const show = tableColumns.show;

  useEffect(() => {
    let cancelled = false;
    getLocationEvents(slug)
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
          rememberLocationHint({ slug: payload.slug, name: payload.name });
          applyPageMeta(locationPageMeta(payload, { eventsLog: true }));
          flushMetrikaHit();
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
        // Просмотр был, пусть и неудачный — досылаем с родовым заголовком.
        flushMetrikaHit();
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

  // Сквозной номер в скобках рассказывает ровно об одном: локация сменила
  // систему и счёт стартов там пошёл заново. У площадки, прожившей всю
  // историю в одной системе, он просто повторяет цифру слева (просьба
  // Дмитрия 22.08.2026) — там колонка остаётся с одним номером.
  const showOverallNumber = platformCounts.size > 1;

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

  const visibleCount = EVENTS_COLUMNS.filter((column) => show(column.key)).length;

  // Пока данные едут, имя берём из подсказки — иначе подпункт сайдбара с
  // названием площадки мигает при каждом переходе внутри локации.
  const sidebarLocation = data
    ? { slug: data.slug, name: data.name }
    : locationHintFor(slug);

  const sortProps = (key: SortKey) => ({
    filterable: false,
    sortActive: sort.key === key,
    sortAsc: sort.asc,
    onSort: () => toggleSort(key),
  });

  if (notFound) {
    return (
      <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
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
      <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
        <div className="card error">
          <p>{error}</p>
        </div>
      </PortalSectionShell>
    );
  }

  if (!data) {
    return (
      <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
        <p className="muted">Загрузка…</p>
      </PortalSectionShell>
    );
  }

  const viewTabs = (
    <div className="aj-tabs loc-events-view" role="tablist" aria-label="Вид журнала">
      {(
        [
          { value: "protocols", label: "Протоколы" },
          { value: "attendance", label: "Посещаемость" },
        ] as const
      ).map((tab) => (
        <button
          key={tab.value}
          type="button"
          role="tab"
          aria-selected={view === tab.value}
          className={`aj-tab${view === tab.value ? " aj-tab-active" : ""}`}
          onClick={() => switchView(tab.value)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );

  return (
    <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
      <header className="loc-header loc-wide-page">
        <p className="muted loc-header-breadcrumb">
          <a href="/locations">← Все локации</a> /{" "}
          <a href={`/locations/${data.slug}`}>{data.name}</a> / Журнал
        </p>
        <div className="loc-header-title">
          <h1>{data.name} — журнал протоколов</h1>
        </div>
      </header>

      {/* Два вида одного журнала: «Протоколы» — сводка по стартам,
          «Посещаемость» — матрица «участник × даты». Тот же приём, что в
          рейтингах пробежек и туризма. */}
      {/* Вид журнала и фильтр систем — в одной панели, как на прочих витринах.
          В режиме «Посещаемость» ту же панель рисует сам журнал (свои фильтры
          «Год» и «Кого показывать» он держит там же), поэтому переключатель
          вида уезжает к нему пропом. */}
      {view === "protocols" ? (
      <FilterPanel>
        <FilterRow>
        <FilterGroup label="Вид">
      {viewTabs}
        </FilterGroup>
        {view === "protocols" && platformCounts.size > 1 && (
          <FilterGroup label="Система">
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
                  {platformCodeLabel(code)} ({formatInt(count)})
                </button>
              ))}
            </div>
          </FilterGroup>
        )}
          {view === "protocols" && tableColumns.hasToggle && (
            <FilterGroup label="Колонки">
              <TableViewToggle columns={tableColumns} inline />
            </FilterGroup>
          )}
        </FilterRow>
      </FilterPanel>
      ) : null}

      {view === "attendance" ? (
        <section className="loc-section loc-attendance-section">
          <p className="muted loc-attendance-intro">
            Кто был на каждом старте этой площадки: пробежки и волонтёрства по
            датам. Свежие даты слева, наведение или тап на клетку подсвечивает
            весь столбец — видно, кто был в конкретную дату.
          </p>
          <LocationAttendanceJournal slug={slug} viewTabs={viewTabs} />
        </section>
      ) : (
        <>
      <section className="loc-section">
        <TableWrap
          innerRef={attachFloatingHead}
          outerRef={tableColumns.measureRef}
          className="loc-events-wrap"
          stickyFirstCol={showFull}
        >
          <table
            className={`data-table data-table-layout-fixed loc-events-table${
              showFull ? "" : " data-table-short"
            }`}
            style={showFull ? undefined : { minWidth: tableColumns.minWidth }}
          >
            <colgroup>
              <col className="col-number" />
              <col className="col-date" />
              {show("platform") && <col className="col-platform" />}
              <col className="col-compact" />
              {show("volunteers") && <col className="col-compact" />}
              {show("newcomers") && <col className="col-compact" />}
              {show("best_male") && <col className="col-time" />}
              {show("best_female") && <col className="col-time" />}
              {show("avg") && <col className="col-time" />}
              {show("prs") && <col className="col-compact-wide" />}
            </colgroup>
            <thead>
              <tr>
                <ColumnHeader
                  label="№"
                  headerTitle={
                    showOverallNumber
                      ? "Номер события в системе; в скобках — сквозной номер старта локации по всем системам"
                      : "Номер события в системе"
                  }
                  filterable={false}
                />
                <ColumnHeader label="Дата" {...sortProps("date")} />
                {show("platform") && <ColumnHeader label="Система" filterable={false} />}
                <ColumnHeader
                  label="Финишёров"
                  hint="Финишёров на старте"
                  {...sortProps("finishers")}
                />
                {show("volunteers") && (
                  <ColumnHeader
                    label="Волонтёров"
                    hint="Волонтёров на старте"
                    {...sortProps("volunteers")}
                  />
                )}
                {show("newcomers") && (
                  <ColumnHeader
                    label="Новичков"
                    hint="Новички: дебютанты движения + впервые на этой локации"
                    {...sortProps("newcomers")}
                  />
                )}
                {show("best_male") && (
                  <ColumnHeader
                    label="Лучшее время (М)"
                    hint="Лучшее время среди мужчин"
                    {...sortProps("best_male")}
                  />
                )}
                {show("best_female") && (
                  <ColumnHeader
                    label="Лучшее время (Ж)"
                    hint="Лучшее время среди женщин"
                    {...sortProps("best_female")}
                  />
                )}
                {show("avg") && (
                  <ColumnHeader
                    label="Среднее время"
                    hint="Среднее время финиша"
                    {...sortProps("avg")}
                  />
                )}
                {show("prs") && (
                  <ColumnHeader
                    label="Личных рекордов"
                    hint="Личных рекордов установлено в этот день"
                    {...sortProps("prs")}
                  />
                )}
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={visibleCount} className="table-empty-cell">
                    <span className="muted">Нет стартов по выбранному фильтру</span>
                  </td>
                </tr>
              ) : (
                rows.map((row) => (
                  <tr key={`${row.platform_code}-${row.event_date}`}>
                    <td className="td-compact">
                      <span className="loc-events-number">
                        {row.event_number ?? "—"}
                        {showOverallNumber && (
                          <StatHintTooltip text="Сквозной номер старта — какой это по счёту старт локации за всю историю, по всем системам вместе">
                            <span className="muted">({row.overall_number})</span>
                          </StatHintTooltip>
                        )}
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
                      {/* Есть полный протокол — дата ведёт на НАШ протокол;
                          внешняя ссылка платформы остаётся на его странице. */}
                      {row.has_protocol ? (
                        <a
                          className="activity-date-link"
                          href={`/locations/${encodeURIComponent(data.slug)}/protocol/${row.platform_code}/${row.event_date}`}
                          title="Открыть протокол старта"
                        >
                          {formatDate(row.event_date)}
                        </a>
                      ) : (
                        <ActivityDateLink date={row.event_date} url={row.protocol_url} />
                      )}
                    </td>
                    {show("platform") && (
                      <td className="td-platform">
                        <PlatformBadge code={row.platform_code} />
                      </td>
                    )}
                    <td className="td-compact">{row.finishers ?? "—"}</td>
                    {show("volunteers") && <td className="td-compact">{row.volunteers ?? "—"}</td>}
                    {show("newcomers") && <td className="td-compact">{rowNewcomers(row) ?? "—"}</td>}
                    {show("best_male") && (
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
                    )}
                    {show("best_female") && (
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
                    )}
                    {show("avg") && <td className="td-time">{stripHours(row.avg_time_display)}</td>}
                    {show("prs") && <td className="td-compact">{row.prs ?? "—"}</td>}
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
        </>
      )}
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
