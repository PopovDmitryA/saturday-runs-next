import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckboxListFilter } from "../../components/activityTable/CheckboxListFilter";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { ActivityDateLink } from "../../components/ActivityDateLink";
import { ActivityDateCell } from "../../components/ActivityDateCell";
import { AppShell } from "../../components/AppShell";
import { EmptyActivityState } from "../../components/EmptyActivityState";
import { GlobalPrFinishTime } from "../../components/GlobalPrFinishTime";
import { LocationNameLink } from "../../components/LocationNameLink";
import { LocationPrLocationName } from "../../components/LocationPrLocationName";
import { PlatformBadge } from "../../components/PlatformBadge";
import { RateRunModal } from "../../components/RateRunModal";
import { RunRatingStar } from "../../components/RunRatingStar";
import { Snackbar } from "../../components/Snackbar";
import { useActivityFilters } from "../../hooks/useActivityFilters";
import { useSnackbar } from "../../hooks/useSnackbar";
import {
  getEligibleRuns,
  getMyRatings,
  listProfileLinks,
  runEntryId,
  type EligibleRun,
  type MyRating,
  type RunItem,
} from "../../lib/api";
import { useAppDataSource } from "../../lib/appDataSource";
import { createFullSelection, sortRuns, toggleDateSort, toggleFinishSort, togglePaceSort, togglePositionSort, uniquePlatforms } from "../../lib/activityList";
import { formatFinishTimeValue, platformCodeLabel } from "../../lib/format";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { TableViewToggle, useTableView } from "../../components/tableUx/TableViewToggle";
import { useAdaptiveColumns, type AdaptiveColumn } from "../../components/tableUx/useAdaptiveColumns";

// bare — отдать только тело страницы, без AppShell: портальный ЛК (/new/*)
// оборачивает контент в собственный каркас с сайдбаром.
// Колонки «Пробежек» в порядке важности: дата, локация и время — всегда,
// дальше добавляем по мере ширины. Ширины совпадают с CSS (.runs-table).
const RUNS_COLUMNS: AdaptiveColumn[] = [
  { key: "date", width: 160, required: true },
  { key: "location", width: 200, required: true },
  { key: "time", width: 112, required: true },
  { key: "position", width: 104 },
  { key: "platform", width: 112 },
  { key: "pace", width: 120 },
  { key: "gender_position", width: 136 },
  { key: "rating", width: 36 },
];

function RunsContent({ bare = false }: { bare?: boolean } = {}) {
  const { listRuns, mode } = useAppDataSource();
  const isDemo = mode === "demo";
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [hasProfileLink, setHasProfileLink] = useState(false);
  const [includeTest, setIncludeTest] = useState(false);
  const [includeDuplicates, setIncludeDuplicates] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Оценка стартов — только в своём разделе пробежек.
  const showRating = mode === "auth";
  const [ratingsMap, setRatingsMap] = useState<Map<string, MyRating>>(new Map());
  const [canRate, setCanRate] = useState(false);
  // entry_id стартов, доступных к оценке прямо сейчас. Правило (окно 30 дней +
  // добор по одному на неоценённую локацию) живёт на бэке — здесь только membership.
  const [eligibleIds, setEligibleIds] = useState<Set<string>>(new Set());
  const [ratingsVersion, setRatingsVersion] = useState(0);
  const [activeRun, setActiveRun] = useState<EligibleRun | null>(null);
  const { snackbar, showSnackbar, dismissSnackbar } = useSnackbar();

  const allPlatforms = useMemo(() => uniquePlatforms(runs), [runs]);

  const filters = useActivityFilters(runs);
  const [tableView, setTableView] = useTableView("runs");
  // Краткий вид набирает колонки под ширину блока (единый механизм со всеми
  // таблицами сайта), «Полно» — весь набор с горизонтальным скроллом.
  const adaptive = useAdaptiveColumns(RUNS_COLUMNS);
  const showFull = tableView === "full";
  const show = (key: string) => showFull || adaptive.isVisible(key);
  // «★» рисуется, только если оценки вообще доступны, — иначе колонка пустая.
  const visibleColumnCount = RUNS_COLUMNS.filter(
    (column) => show(column.key) && (column.key !== "rating" || showRating),
  ).length;

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
      if (isDemo || mode === "public-profile") {
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

  useEffect(() => {
    if (!showRating) {
      return;
    }
    let cancelled = false;
    Promise.all([getMyRatings(), getEligibleRuns()])
      .then(([res, eligible]) => {
        if (cancelled) {
          return;
        }
        setCanRate(res.can_rate);
        setEligibleIds(new Set(eligible.runs.map((item) => item.entry_id)));
        // В таблице пробежек показываем только оценки-пробежки (у волонтёрских
        // run_result_id пуст — они живут в блоке «Оцените недавние старты»).
        setRatingsMap(
          new Map(
            res.ratings
              .filter((item) => item.run_result_id != null)
              .map((item) => [item.run_result_id as string, item]),
          ),
        );
      })
      .catch(() => {
        // тихо — звёзды просто не покажутся
      });
    return () => {
      cancelled = true;
    };
  }, [showRating, ratingsVersion]);

  // После сохранения/удаления перечитываем оценки (проще, чем мержить локально).
  const reloadRatings = useCallback(() => setRatingsVersion((v) => v + 1), []);

  // Старт доступен к оценке, если бэк вернул его в списке доступных.
  const isEligible = useCallback(
    (runResultId: string) => eligibleIds.has(runEntryId(runResultId)),
    [eligibleIds],
  );

  const buildEligibleRun = useCallback(
    (run: RunItem, rating: MyRating | undefined): EligibleRun => ({
      entry_id: runEntryId(run.run_result_id ?? ""),
      participation_type: "run",
      run_result_id: run.run_result_id ?? null,
      event_date: run.event_date,
      platform_code: run.platform_code,
      location_name: run.location_name,
      location_city: run.location_city,
      finish_time_display: run.finish_time_display,
      position: run.position,
      is_pr: run.is_pr,
      volunteer_role: null,
      event_url: run.event_url ?? null,
      my_rating: rating ?? null,
    }),
    [],
  );

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

          <TableViewToggle value={tableView} onChange={setTableView} alwaysVisible />
          <TableWrap stickyFirstCol={showFull} outerRef={adaptive.measureRef}>
            <table
              className={`data-table data-table-filterable data-table-layout-fixed runs-table${
                showFull ? "" : " data-table-short"
              }`}
              style={showFull ? undefined : { minWidth: adaptive.minWidth }}
            >
              <colgroup>
                <col className="col-date" />
                {show("platform") && <col className="col-platform" />}
                <col className="col-location" />
                {show("position") && <col className="col-compact" />}
                {show("gender_position") && <col className="col-gender" />}
                <col className="col-time" />
                {show("pace") && <col className="col-pace" />}
                {show("rating") && showRating && <col className="col-rating" />}
              </colgroup>
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
                  {show("platform") && <ColumnHeader label="Система" filterable={false} />}
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
                  {show("position") && (
                    <ColumnHeader
                      label="Место"
                      filterable={false}
                      sortActive={positionSortActive}
                      sortAsc={filters.sort === "position_asc"}
                      onSort={() => filters.setSort((current) => togglePositionSort(current))}
                    />
                  )}
                  {show("gender_position") && (
                    <ColumnHeader
                      label="Место (пол)"
                      filterable={false}
                      hint="Место среди своего пола"
                    />
                  )}
                  <ColumnHeader
                    label="Время"
                    filterable={false}
                    sortActive={finishSortActive}
                    sortAsc={filters.sort === "finish_asc"}
                    onSort={() => filters.setSort((current) => toggleFinishSort(current))}
                  />
                  {show("pace") && (
                    <ColumnHeader
                      label="Темп"
                      filterable={false}
                      sortActive={paceSortActive}
                      sortAsc={filters.sort === "pace_asc"}
                      onSort={() => filters.setSort((current) => togglePaceSort(current))}
                    />
                  )}
                  {show("rating") && showRating && (
                    <ColumnHeader
                      label="★"
                      filterable={false}
                      headerTitle="Оценка старта — жёлтая звезда, если вы оценили"
                    />
                  )}
                </tr>
              </thead>
              <tbody>
                {displayedRuns.length === 0 ? (
                  <tr>
                    <td colSpan={visibleColumnCount} className="table-empty-cell">
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
                      {show("platform") && (
                        <td className="td-platform">
                          <PlatformBadge code={run.platform_code} />
                        </td>
                      )}
                      <td className="td-location">
                        <LocationPrLocationName isLocationPr={run.is_location_pr}>
                          <LocationNameLink name={run.location_name} slug={run.location_slug} />
                        </LocationPrLocationName>
                      </td>
                      {show("position") && <td className="td-compact">{run.position ?? "—"}</td>}
                      {show("gender_position") && (
                        <td className="td-compact">{run.gender_position ?? "—"}</td>
                      )}
                      <td className="td-time">
                        <GlobalPrFinishTime isGlobalPr={run.is_global_pr}>
                          {formatFinishTimeValue(run.finish_time_display, run.finish_time_sec)}
                        </GlobalPrFinishTime>
                      </td>
                      {show("pace") && (
                        <td className="td-pace">
                          {run.pace_display ? `${run.pace_display} /км` : "—"}
                        </td>
                      )}
                      {show("rating") &&
                        showRating &&
                        (() => {
                          const rating = run.run_result_id
                            ? ratingsMap.get(run.run_result_id)
                            : undefined;
                          const canCreate =
                            canRate && !rating && !!run.run_result_id && isEligible(run.run_result_id);
                          return (
                            <td className="td-rating">
                              <RunRatingStar
                                rating={rating}
                                canCreate={canCreate}
                                canRate={canRate}
                                onOpen={() => setActiveRun(buildEligibleRun(run, rating))}
                              />
                            </td>
                          );
                        })()}
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </TableWrap>

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

      {activeRun && (
        <RateRunModal
          run={activeRun}
          onClose={() => setActiveRun(null)}
          onSaved={() => {
            reloadRatings();
            setActiveRun(null);
            showSnackbar({ variant: "default", title: "Спасибо!", message: "Отзыв сохранён" });
          }}
          onDeleted={() => {
            reloadRatings();
            setActiveRun(null);
          }}
        />
      )}

      <Snackbar open={snackbar.open} title={snackbar.title} variant={snackbar.variant} onDismiss={dismissSnackbar}>
        {snackbar.message}
      </Snackbar>
    </>
  );

  if (bare || mode === "public-profile") {
    return <>{pageBody}</>;
  }

  return <AppShell title="Пробежки">{pageBody}</AppShell>;
}

export { RunsContent };

