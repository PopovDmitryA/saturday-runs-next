import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityTableCols } from "../../components/activityTable/ActivityTableCols";
import { CheckboxListFilter } from "../../components/activityTable/CheckboxListFilter";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { ActivityDateLink } from "../../components/ActivityDateLink";
import { ActivityDateCell } from "../../components/ActivityDateCell";
import { AppShell } from "../../components/AppShell";
import { EmptyActivityState } from "../../components/EmptyActivityState";
import { GlobalPrFinishTime } from "../../components/GlobalPrFinishTime";
import { RequireAuth } from "../../components/RequireAuth";
import { PlatformBadge } from "../../components/PlatformBadge";
import { useActivityFilters } from "../../hooks/useActivityFilters";
import { listProfileLinks, type RunItem } from "../../lib/api";
import { useAppDataSource, AppDataSourceProvider, demoDataSource } from "../../lib/appDataSource";
import { createFullSelection, sortRuns, toggleDateSort, toggleFinishSort, togglePaceSort, togglePositionSort, uniquePlatforms } from "../../lib/activityList";
import { formatFinishTimeValue, platformCodeLabel } from "../../lib/format";
import { DemoShell } from "../demo/DemoShell";

function RunsContent() {
  const { listRuns, mode } = useAppDataSource();
  const isDemo = mode === "demo";
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [hasProfileLink, setHasProfileLink] = useState(false);
  const [includeTest, setIncludeTest] = useState(false);
  const [includeDuplicates, setIncludeDuplicates] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const allPlatforms = useMemo(() => uniquePlatforms(runs), [runs]);

  const filters = useActivityFilters(runs);

  const displayedRuns = useMemo(
    () =>
      sortRuns(
        includeDuplicates ? filters.filtered : filters.filtered.filter((r) => !r.is_crosslinked),
        filters.sort,
      ),
    [filters.filtered, filters.sort, includeDuplicates],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listRuns(includeTest);
      setRuns(data);
      if (isDemo || mode === "public-profile" || mode === "admin-preview") {
        setHasProfileLink(false);
      } else {
        const links = await listProfileLinks();
        setHasProfileLink(links.length > 0);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить пробежки");
    } finally {
      setLoading(false);
    }
  }, [includeTest, isDemo, mode, listRuns]);

  useEffect(() => {
    void load();
  }, [load]);

  const showEmpty = !loading && !error && runs.length === 0;
  const dateSortActive = filters.sort === "date_desc" || filters.sort === "date_asc";
  const finishSortActive = filters.sort === "finish_asc" || filters.sort === "finish_desc";
  const paceSortActive = filters.sort === "pace_asc" || filters.sort === "pace_desc";
  const positionSortActive = filters.sort === "position_asc" || filters.sort === "position_desc";

  const visibleRunCount = useMemo(
    () => (includeDuplicates ? runs : runs.filter((r) => !r.is_crosslinked)).length,
    [runs, includeDuplicates],
  );

  const platformRunCounts = useMemo(() => {
    const base = includeDuplicates ? runs : runs.filter((r) => !r.is_crosslinked);
    const counts: Record<string, number> = {};
    for (const run of base) {
      counts[run.platform_code] = (counts[run.platform_code] ?? 0) + 1;
    }
    return allPlatforms.map((code) => ({ code, count: counts[code] ?? 0 }));
  }, [runs, includeDuplicates, allPlatforms]);

  const activePlatformFilter =
    filters.platformFilterActive
      ? [...filters.selectedPlatforms][0] ?? "all"
      : "all";

  const pageBody = (
    <>
      {!isDemo && (
        <div className="checkbox-row-group">
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={includeTest}
              onChange={(event) => setIncludeTest(event.target.checked)}
            />
            Показывать тестовые мероприятия
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={includeDuplicates}
              onChange={(event) => setIncludeDuplicates(event.target.checked)}
            />
            Показывать незачётные пробежки
          </label>
        </div>
      )}

      {loading && <p className="muted">Загрузка…</p>}

      {error && (
        <div className="card error">
          <p>{error}</p>
          <button type="button" className="btn secondary" onClick={() => void load()}>
            Повторить
          </button>
        </div>
      )}

      {showEmpty &&
        (isDemo ? (
          <div className="card">
            <p className="muted">В демо-профиле нет пробежек для отображения.</p>
          </div>
        ) : (
          <EmptyActivityState activityLabel="Пробежек" hasProfileLink={hasProfileLink} />
        ))}

      {!loading && !error && runs.length > 0 && (
        <>
          <div className="map-mode-tabs" role="tablist" aria-label="Фильтр по системам">
            <button
              type="button"
              role="tab"
              aria-selected={activePlatformFilter === "all"}
              className={activePlatformFilter === "all" ? "map-mode-tab active" : "map-mode-tab"}
              onClick={() => filters.setSelectedPlatforms(createFullSelection(allPlatforms))}
            >
              Все ({visibleRunCount})
            </button>
            {platformRunCounts.map(({ code, count }) => (
              <button
                key={code}
                type="button"
                role="tab"
                aria-selected={activePlatformFilter === code}
                className={activePlatformFilter === code ? "map-mode-tab active" : "map-mode-tab"}
                onClick={() => filters.setSelectedPlatforms(new Set([code]))}
              >
                {platformCodeLabel(code)} ({count})
              </button>
            ))}
          </div>

          <div className="table-wrap">
            <table className="data-table data-table-filterable data-table-layout-fixed">
              <ActivityTableCols variant="runs" />
              <thead>
                <tr>
                  <ColumnHeader
                    label="Дата"
                    filterActive={filters.dateFilterActive}
                    sortActive={dateSortActive}
                    sortAsc={filters.sort === "date_asc"}
                    onSort={() => filters.setSort((current) => toggleDateSort(current))}
                    filterTitle="Фильтр по дате"
                    filterContent={
                      <div className="date-filter-fields">
                        <label className="filter-field">
                          <span className="filter-field-label">С</span>
                          <input
                            className="filter-field-input"
                            type="date"
                            value={filters.dateFrom}
                            onChange={(event) => filters.setDateFrom(event.target.value)}
                          />
                        </label>
                        <label className="filter-field">
                          <span className="filter-field-label">По</span>
                          <input
                            className="filter-field-input"
                            type="date"
                            value={filters.dateTo}
                            onChange={(event) => filters.setDateTo(event.target.value)}
                          />
                        </label>
                      </div>
                    }
                    filterFooter={
                      filters.dateFilterActive ? (
                        <button
                          type="button"
                          className="filter-popover-link"
                          onClick={() => {
                            filters.setDateFrom("");
                            filters.setDateTo("");
                          }}
                        >
                          Сбросить фильтр
                        </button>
                      ) : undefined
                    }
                  />
                  <ColumnHeader
                    label="Система"
                    filterable={false}
                  />
                  <ColumnHeader
                    label="Локация"
                    filterActive={filters.locationFilterActive}
                    filterTitle="Фильтр по локации"
                    filterContent={
                      <CheckboxListFilter
                        options={filters.locationOptions}
                        selected={filters.selectedLocations}
                        onSelectedChange={filters.setSelectedLocations}
                        searchPlaceholder="Поиск локации…"
                      />
                    }
                  />
                  <ColumnHeader
                    label="Место"
                    filterable={false}
                    sortActive={positionSortActive}
                    sortAsc={filters.sort === "position_asc"}
                    onSort={() => filters.setSort((current) => togglePositionSort(current))}
                  />
                  <ColumnHeader label="Место (пол)" filterable={false} />
                  <ColumnHeader
                    label="Время"
                    filterable={false}
                    sortActive={finishSortActive}
                    sortAsc={filters.sort === "finish_asc"}
                    onSort={() => filters.setSort((current) => toggleFinishSort(current))}
                  />
                  <ColumnHeader
                    label="Темп"
                    filterable={false}
                    sortActive={paceSortActive}
                    sortAsc={filters.sort === "pace_asc"}
                    onSort={() => filters.setSort((current) => togglePaceSort(current))}
                  />
                </tr>
              </thead>
              <tbody>
                {displayedRuns.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="table-empty-cell">
                      <span className="muted">Нет строк по фильтрам</span>
                      {filters.hasActiveFilters && (
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm table-empty-reset"
                          onClick={filters.resetFilters}
                        >
                          Сбросить
                        </button>
                      )}
                    </td>
                  </tr>
                ) : (
                  displayedRuns.map((run, index) => (
                    <tr
                      key={`${run.platform_code}-${run.event_date}-${run.location_name}-${index}`}
                      className={run.is_crosslinked ? "run-crosslinked" : undefined}
                    >
                      <td className="td-date">
                        <ActivityDateCell
                          date={<ActivityDateLink date={run.event_date} url={run.event_url} />}
                          badges={
                            <>
                              {run.is_test_event && <span className="badge">тест</span>}
                              {run.is_pr && <span className="badge badge-pr">PR</span>}
                              {run.is_crosslinked && (
                                <span
                                  className="badge badge-crosslinked"
                                  title="Пробежка не учтена в общем зачёте — в этот день учтена пробежка по другой системе на той же локации"
                                >
                                  не в зачёте
                                </span>
                              )}
                            </>
                          }
                        />
                      </td>
                      <td className="td-platform">
                        <PlatformBadge code={run.platform_code} />
                      </td>
                      <td className="td-location">{run.location_name}</td>
                      <td className="td-compact">{run.position ?? "—"}</td>
                      <td className="td-compact">{run.gender_position ?? "—"}</td>
                      <td className="td-time">
                        <GlobalPrFinishTime isGlobalPr={run.is_global_pr}>
                          {formatFinishTimeValue(run.finish_time_display, run.finish_time_sec)}
                        </GlobalPrFinishTime>
                      </td>
                      <td className="td-pace">
                        {run.pace_display ? `${run.pace_display} /км` : "—"}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          <p className="table-foot muted">
            <span>
              Показано: {displayedRuns.length} из {visibleRunCount}
            </span>
            {filters.hasActiveFilters && (
              <button type="button" className="btn btn-ghost btn-sm" onClick={filters.resetAll}>
                Сбросить всё
              </button>
            )}
          </p>
        </>
      )}
    </>
  );

  if (isDemo) {
    return <DemoShell title="Пробежки">{pageBody}</DemoShell>;
  }

  if (mode === "public-profile" || mode === "admin-preview") {
    return <>{pageBody}</>;
  }

  return <AppShell title="Пробежки">{pageBody}</AppShell>;
}

export { RunsContent };

export function RunsPage() {
  return <RequireAuth>{() => <RunsContent />}</RequireAuth>;
}

export function DemoRunsPage() {
  return (
    <AppDataSourceProvider source={demoDataSource}>
      <RunsContent />
    </AppDataSourceProvider>
  );
}
