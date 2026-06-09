import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityTableCols } from "../../components/activityTable/ActivityTableCols";
import { CheckboxListFilter } from "../../components/activityTable/CheckboxListFilter";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { ActivityDateLink } from "../../components/ActivityDateLink";
import { AppShell } from "../../components/AppShell";
import { EmptyActivityState } from "../../components/EmptyActivityState";
import { RequireAuth } from "../../components/RequireAuth";
import { PlatformBadge } from "../../components/PlatformBadge";
import { useVolunteeringFilters } from "../../hooks/useVolunteeringFilters";
import { listProfileLinks, type VolunteeringItem } from "../../lib/api";
import { useAppDataSource, AppDataSourceProvider, demoDataSource } from "../../lib/appDataSource";
import { sortVolunteering, toggleDateSort } from "../../lib/activityList";
import { DemoShell } from "../demo/DemoShell";

function VolunteeringContent() {
  const { listVolunteering, mode } = useAppDataSource();
  const isDemo = mode === "demo";
  const [items, setItems] = useState<VolunteeringItem[]>([]);
  const [hasProfileLink, setHasProfileLink] = useState(false);
  const [includeTest, setIncludeTest] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const filters = useVolunteeringFilters(items);
  const displayedItems = useMemo(
    () => sortVolunteering(filters.filtered, filters.sort),
    [filters.filtered, filters.sort],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listVolunteering(includeTest);
      setItems(data);
      if (isDemo) {
        setHasProfileLink(true);
      } else {
        const links = await listProfileLinks();
        setHasProfileLink(links.length > 0);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить волонтёрства");
    } finally {
      setLoading(false);
    }
  }, [includeTest, isDemo, listVolunteering]);

  useEffect(() => {
    void load();
  }, [load]);

  const showEmpty = !loading && !error && items.length === 0;
  const dateSortActive = filters.sort === "date_desc" || filters.sort === "date_asc";

  const pageBody = (
    <>
      {!isDemo && (
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={includeTest}
            onChange={(event) => setIncludeTest(event.target.checked)}
          />
          Показывать тестовые мероприятия
        </label>
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
          <div className="table-wrap">
            <table className="data-table data-table-filterable data-table-layout-fixed data-table-volunteering">
              <ActivityTableCols variant="volunteering" />
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
                    filterActive={filters.platformFilterActive}
                    filterTitle="Фильтр по системе"
                    filterContent={
                      <CheckboxListFilter
                        options={filters.platformOptions}
                        selected={filters.selectedPlatforms}
                        onSelectedChange={filters.setSelectedPlatforms}
                      />
                    }
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
                </tr>
              </thead>
              <tbody>
                {displayedItems.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="table-empty-cell">
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
                    <tr key={`${item.platform_code}-${item.event_date}-${item.location_name}-${index}`}>
                      <td className="td-date">
                        <ActivityDateLink date={item.event_date} url={item.event_url} />
                        {item.is_test_event && <span className="badge">тест</span>}
                      </td>
                      <td className="td-platform">
                        <PlatformBadge code={item.platform_code} />
                      </td>
                      <td className="td-location">{item.location_name}</td>
                      <td className="td-role">{item.role ?? "—"}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          <p className="table-foot muted">
            <span>
              Показано: {displayedItems.length} из {items.length}
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
    return <DemoShell title="Волонтёрство">{pageBody}</DemoShell>;
  }

  return <AppShell title="Волонтёрство">{pageBody}</AppShell>;
}

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
