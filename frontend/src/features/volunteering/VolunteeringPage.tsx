import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityTableCols } from "../../components/activityTable/ActivityTableCols";
import { CheckboxListFilter } from "../../components/activityTable/CheckboxListFilter";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { ActivityDateLink } from "../../components/ActivityDateLink";
import { AppShell } from "../../components/AppShell";
import { EmptyActivityState } from "../../components/EmptyActivityState";
import { LocationNameLink } from "../../components/LocationNameLink";
import { RequireAuth } from "../../components/RequireAuth";
import { PlatformBadge } from "../../components/PlatformBadge";
import { RateRunModal } from "../../components/RateRunModal";
import { RunRatingStar } from "../../components/RunRatingStar";
import { useVolunteeringFilters } from "../../hooks/useVolunteeringFilters";
import {
  getEligibleRuns,
  getMyRatings,
  listProfileLinks,
  type EligibleRun,
  type MyRating,
  type VolunteeringItem,
} from "../../lib/api";
import { useAppDataSource, AppDataSourceProvider, demoDataSource } from "../../lib/appDataSource";
import { createFullSelection, sortVolunteering, toggleDateSort, uniquePlatforms } from "../../lib/activityList";
import { platformCodeLabel } from "../../lib/format";
import { DemoShell } from "../demo/DemoShell";

function VolunteeringContent() {
  const { listVolunteering, mode } = useAppDataSource();
  const isDemo = mode === "demo";
  const [items, setItems] = useState<VolunteeringItem[]>([]);
  const [hasProfileLink, setHasProfileLink] = useState(false);
  const [includeTest, setIncludeTest] = useState(false);
  const [includeDuplicates, setIncludeDuplicates] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Оценка стартов — только в своём разделе волонтёрств.
  const showRating = mode === "auth";
  const [ratingsMap, setRatingsMap] = useState<Map<string, MyRating>>(new Map());
  const [canRate, setCanRate] = useState(false);
  // entry_id стартов, доступных к оценке прямо сейчас (правило считает бэк).
  const [eligibleIds, setEligibleIds] = useState<Set<string>>(new Set());
  const [ratingsVersion, setRatingsVersion] = useState(0);
  const [activeRun, setActiveRun] = useState<EligibleRun | null>(null);

  // parkrun volunteering: counted but not shown in table
  // count taken from "Total Credits" summary row, e.g. role = "Total Credits (2×)"
  const tableItems = useMemo(() => items.filter((i) => i.platform_code !== "parkrun"), [items]);
  const parkrunCount = useMemo(() => {
    const totalCreditsRow = items.find(
      (i) => i.platform_code === "parkrun" && i.role?.startsWith("Total Credits"),
    );
    if (totalCreditsRow?.role) {
      const match = /\((\d+)×\)/.exec(totalCreditsRow.role);
      if (match) return parseInt(match[1], 10);
    }
    return items.filter((i) => i.platform_code === "parkrun").length;
  }, [items]);

  const allPlatforms = useMemo(() => uniquePlatforms(tableItems), [tableItems]);

  const filters = useVolunteeringFilters(tableItems);

  const displayedItems = useMemo(
    () =>
      sortVolunteering(
        includeDuplicates ? filters.filtered : filters.filtered.filter((i) => !i.is_crosslinked),
        filters.sort,
      ),
    [filters.filtered, filters.sort, includeDuplicates],
  );

  const visibleVolCount = useMemo(
    () => (includeDuplicates ? tableItems : tableItems.filter((i) => !i.is_crosslinked)).length,
    [tableItems, includeDuplicates],
  );

  const platformCounts = useMemo(() => {
    const base = includeDuplicates ? tableItems : tableItems.filter((i) => !i.is_crosslinked);
    const counts: Record<string, number> = {};
    for (const item of base) counts[item.platform_code] = (counts[item.platform_code] ?? 0) + 1;
    return allPlatforms.map((code) => ({ code, count: counts[code] ?? 0 }));
  }, [tableItems, includeDuplicates, allPlatforms]);

  const activePlatformFilter =
    filters.platformFilterActive ? [...filters.selectedPlatforms][0] ?? "all" : "all";

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listVolunteering(includeTest);
      setItems(data);
      if (isDemo || mode === "public-profile") {
        setHasProfileLink(false);
      } else {
        const links = await listProfileLinks();
        setHasProfileLink(links.length > 0);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить волонтёрства");
    } finally {
      setLoading(false);
    }
  }, [includeTest, isDemo, mode, listVolunteering]);

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
        setEligibleIds(new Set(eligible.runs.map((entry) => entry.entry_id)));
        // В таблице волонтёрств показываем только волонтёрские оценки
        // (run_result_id пуст), keyed по entry_id.
        setRatingsMap(
          new Map(
            res.ratings
              .filter((item) => item.participation_type === "volunteer")
              .map((item) => [item.entry_id, item]),
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

  const reloadRatings = useCallback(() => setRatingsVersion((v) => v + 1), []);

  // Волонтёрство доступно к оценке, если бэк вернул его в списке доступных.
  const isEligible = useCallback(
    (ratingEntryId: string) => eligibleIds.has(ratingEntryId),
    [eligibleIds],
  );

  const buildEligibleRun = useCallback(
    (item: VolunteeringItem, rating: MyRating | undefined): EligibleRun => ({
      entry_id: item.rating_entry_id ?? "",
      participation_type: "volunteer",
      run_result_id: null,
      event_date: item.event_date,
      platform_code: item.platform_code,
      location_name: item.location_name,
      location_city: item.location_city,
      finish_time_display: null,
      position: null,
      is_pr: false,
      volunteer_role: item.role,
      event_url: item.event_url ?? null,
      my_rating: rating ?? null,
    }),
    [],
  );

  const showEmpty = !loading && !error && items.length === 0;
  const dateSortActive = filters.sort === "date_desc" || filters.sort === "date_asc";

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
            Показывать незачётные волонтёрства
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
            <p className="muted">В демо-профиле нет записей о волонтёрстве.</p>
          </div>
        ) : (
          <EmptyActivityState activityLabel="Записей о волонтёрстве" hasProfileLink={hasProfileLink} />
        ))}

      {!loading && !error && items.length > 0 && (
        <>
          <div className="map-mode-tabs" role="tablist" aria-label="Фильтр по системам">
            <button
              type="button"
              role="tab"
              aria-selected={activePlatformFilter === "all"}
              className={activePlatformFilter === "all" ? "map-mode-tab active" : "map-mode-tab"}
              onClick={() => filters.setSelectedPlatforms(createFullSelection(allPlatforms))}
            >
              Все ({visibleVolCount})
            </button>
            {platformCounts.map(({ code, count }) => (
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
            {parkrunCount > 0 && (
              <button
                type="button"
                role="tab"
                aria-selected={false}
                className="map-mode-tab"
                disabled
                title="Волонтёрства parkrun учтены в общей статистике, но не отображаются в таблице"
              >
                parkrun ({parkrunCount})
              </button>
            )}
          </div>

          <div className="table-wrap">
            <table className="data-table data-table-filterable data-table-layout-fixed data-table-volunteering">
              <ActivityTableCols variant="volunteering" withRating={showRating} />
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
                    label="Роль"
                    filterActive={filters.roleFilterActive}
                    filterTitle="Фильтр по роли"
                    filterContent={
                      <CheckboxListFilter
                        options={filters.roleOptions}
                        selected={filters.selectedRoles}
                        onSelectedChange={filters.setSelectedRoles}
                        searchPlaceholder="Поиск роли…"
                      />
                    }
                  />
                  {showRating && (
                    <ColumnHeader
                      label="★"
                      filterable={false}
                      headerTitle="Оценка старта — жёлтая звезда, если вы оценили"
                    />
                  )}
                </tr>
              </thead>
              <tbody>
                {displayedItems.length === 0 ? (
                  <tr>
                    <td colSpan={showRating ? 5 : 4} className="table-empty-cell">
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
                  displayedItems.map((item, index) => (
                    <tr
                      key={`${item.platform_code}-${item.event_date}-${item.location_name}-${index}`}
                      className={item.is_crosslinked ? "run-crosslinked" : undefined}
                    >
                      <td className="td-date">
                        <ActivityDateLink date={item.event_date} url={item.event_url} />
                        {item.is_test_event && <span className="badge">тест</span>}
                        {item.is_crosslinked && (
                          <span
                            className="badge badge-crosslinked"
                            title="Волонтёрство не учтено в общем зачёте — в этот день учтено волонтёрство по другой системе на той же локации"
                          >
                            не в зачёте
                          </span>
                        )}
                      </td>
                      <td className="td-platform">
                        <PlatformBadge code={item.platform_code} />
                      </td>
                      <td className="td-location">
                        <LocationNameLink name={item.location_name} slug={item.location_slug} />
                      </td>
                      <td className="td-role">{item.role ?? "—"}</td>
                      {showRating &&
                        (() => {
                          const rating = item.rating_entry_id
                            ? ratingsMap.get(item.rating_entry_id)
                            : undefined;
                          const canCreate =
                            canRate &&
                            !rating &&
                            !!item.rating_entry_id &&
                            !item.is_crosslinked &&
                            isEligible(item.rating_entry_id);
                          return (
                            <td className="td-rating">
                              <RunRatingStar
                                rating={rating}
                                canCreate={canCreate}
                                canRate={canRate}
                                onOpen={() => setActiveRun(buildEligibleRun(item, rating))}
                              />
                            </td>
                          );
                        })()}
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          <p className="table-foot muted">
            <span>
              Показано: {displayedItems.length} из {visibleVolCount}
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
          }}
          onDeleted={() => {
            reloadRatings();
            setActiveRun(null);
          }}
        />
      )}
    </>
  );

  if (isDemo) {
    return <DemoShell title="Волонтёрство">{pageBody}</DemoShell>;
  }

  if (mode === "public-profile") {
    return <>{pageBody}</>;
  }

  return <AppShell title="Волонтёрство">{pageBody}</AppShell>;
}

export { VolunteeringContent };

export function VolunteeringPage() {
  return <RequireAuth>{() => <VolunteeringContent />}</RequireAuth>;
}

export function DemoVolunteeringPage() {
  return (
    <AppDataSourceProvider source={demoDataSource}>
      <VolunteeringContent />
    </AppDataSourceProvider>
  );
}
