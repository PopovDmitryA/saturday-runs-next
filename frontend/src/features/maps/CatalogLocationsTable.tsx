import { useMemo, useState } from "react";
import type { CatalogLocationTableRow, CatalogLocationsTableResponse } from "../../lib/api";
import { formatDate, platformCodeLabel } from "../../lib/format";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { CheckboxListFilter } from "../../components/activityTable/CheckboxListFilter";
import { PlatformBadge } from "../../components/PlatformBadge";
import { CatalogLocationsTableCols } from "./CatalogLocationsTableCols";
import { VisitedFilterPopover } from "./VisitedFilterPopover";
import {
  collectUniqueColumnValues,
  collectUniqueLocationOptions,
  collectUniquePlatformOptions,
  defaultCatalogTableSortAsc,
  EMPTY_CATALOG_TABLE_FILTERS,
  filterCatalogTableRows,
  hasActiveCatalogTableFilters,
  resetCatalogTableFilters,
  sortCatalogTableRows,
  toggleCatalogTableSort,
  type CatalogTableFilters,
  type CatalogTableSortKey,
  type VisitedFilter,
} from "./catalogLocationsTableHelpers";
import type { PlatformFilters } from "./mapFilters";

type CatalogLocationsTableProps = {
  data: CatalogLocationsTableResponse | null;
  loading: boolean;
  error: string | null;
  platformFilters: PlatformFilters;
};

function formatLocationsCount(filtered: number, total: number): string {
  if (filtered === total) {
    return String(filtered);
  }
  return `${filtered} из ${total}`;
}

export function CatalogLocationsTable({
  data,
  loading,
  error,
  platformFilters,
}: CatalogLocationsTableProps) {
  const [sortKey, setSortKey] = useState<CatalogTableSortKey>("name");
  const [sortAsc, setSortAsc] = useState(defaultCatalogTableSortAsc("name"));
  const [tableFilters, setTableFilters] = useState<CatalogTableFilters>(EMPTY_CATALOG_TABLE_FILTERS);

  const baseRows = data?.rows ?? [];

  const cityOptions = useMemo(() => collectUniqueColumnValues(baseRows, "city"), [baseRows]);
  const regionOptions = useMemo(() => collectUniqueColumnValues(baseRows, "region"), [baseRows]);
  const locationOptions = useMemo(() => collectUniqueLocationOptions(baseRows), [baseRows]);
  const platformOptions = useMemo(() => collectUniquePlatformOptions(baseRows), [baseRows]);

  const filteredRows = useMemo(() => {
    const filtered = filterCatalogTableRows(baseRows, {
      platformFilters,
      mapMode: "all",
      tableFilters,
    });
    return sortCatalogTableRows(filtered, sortKey, sortAsc);
  }, [baseRows, platformFilters, sortAsc, sortKey, tableFilters]);

  const handleSort = (column: CatalogTableSortKey) => {
    const next = toggleCatalogTableSort(sortKey, sortAsc, column);
    setSortKey(next.sortKey);
    setSortAsc(next.sortAsc);
  };

  const setVisitedFilter = (value: VisitedFilter) => {
    setTableFilters((current) => ({ ...current, visited: value }));
  };

  const filtersActive = hasActiveCatalogTableFilters(tableFilters);
  const countLabel = data ? formatLocationsCount(filteredRows.length, data.total_rows) : null;

  return (
    <details className="card map-locations-list-spoiler">
      <summary className="map-locations-list-summary">
        Список локаций
        {countLabel != null && <span className="map-locations-list-count muted"> ({countLabel})</span>}
      </summary>
      <div className="map-locations-list-body">
        {loading && !data && <p className="muted">Загрузка таблицы…</p>}
        {error && <p className="error-text">{error}</p>}
        {data && (
          <>
            <div className="map-locations-table-toolbar">
              <label className="checkbox-row map-locations-table-option">
                <input
                  type="checkbox"
                  checked={tableFilters.excludePaused}
                  onChange={(event) =>
                    setTableFilters((current) => ({
                      ...current,
                      excludePaused: event.target.checked,
                    }))
                  }
                />
                Скрыть локации на паузе
              </label>
              <button
                type="button"
                className="btn secondary btn-sm map-locations-table-reset"
                onClick={() => setTableFilters(resetCatalogTableFilters())}
                disabled={!filtersActive}
              >
                Сбросить фильтры
              </button>
            </div>
            <div className="table-wrap map-locations-table-wrap">
              <table className="data-table data-table-filterable data-table-layout-fixed data-table-locations">
                <CatalogLocationsTableCols />
                <thead>
                  <tr>
                    <ColumnHeader
                      label="Локация"
                      sortActive={sortKey === "name"}
                      sortAsc={sortAsc}
                      onSort={() => handleSort("name")}
                      filterActive={tableFilters.locations.size > 0}
                      filterTitle="Фильтр по локации"
                      filterContent={
                        <CheckboxListFilter
                          options={locationOptions}
                          selected={tableFilters.locations}
                          onSelectedChange={(locations) =>
                            setTableFilters((current) => ({ ...current, locations }))
                          }
                          searchPlaceholder="Поиск локации…"
                        />
                      }
                      filterFooter={
                        tableFilters.locations.size > 0 ? (
                          <button
                            type="button"
                            className="btn secondary btn-sm"
                            onClick={() =>
                              setTableFilters((current) => ({ ...current, locations: new Set() }))
                            }
                          >
                            Сбросить
                          </button>
                        ) : undefined
                      }
                    />
                    <ColumnHeader
                      label="Город"
                      sortActive={sortKey === "city"}
                      sortAsc={sortAsc}
                      onSort={() => handleSort("city")}
                      filterActive={tableFilters.cities.size > 0}
                      filterContent={
                        <CheckboxListFilter
                          options={cityOptions.map((value) => ({
                            value,
                            label: value || "—",
                          }))}
                          selected={tableFilters.cities}
                          onSelectedChange={(cities) =>
                            setTableFilters((current) => ({ ...current, cities }))
                          }
                        />
                      }
                      filterFooter={
                        tableFilters.cities.size > 0 ? (
                          <button
                            type="button"
                            className="btn secondary btn-sm"
                            onClick={() =>
                              setTableFilters((current) => ({ ...current, cities: new Set() }))
                            }
                          >
                            Сбросить
                          </button>
                        ) : undefined
                      }
                    />
                    <ColumnHeader
                      label="Регион"
                      sortActive={sortKey === "region"}
                      sortAsc={sortAsc}
                      onSort={() => handleSort("region")}
                      filterActive={tableFilters.regions.size > 0}
                      filterContent={
                        <CheckboxListFilter
                          options={regionOptions.map((value) => ({
                            value,
                            label: value || "—",
                          }))}
                          selected={tableFilters.regions}
                          onSelectedChange={(regions) =>
                            setTableFilters((current) => ({ ...current, regions }))
                          }
                        />
                      }
                      filterFooter={
                        tableFilters.regions.size > 0 ? (
                          <button
                            type="button"
                            className="btn secondary btn-sm"
                            onClick={() =>
                              setTableFilters((current) => ({ ...current, regions: new Set() }))
                            }
                          >
                            Сбросить
                          </button>
                        ) : undefined
                      }
                    />
                    <ColumnHeader
                      label="Система"
                      sortActive={sortKey === "platform"}
                      sortAsc={sortAsc}
                      onSort={() => handleSort("platform")}
                      filterActive={tableFilters.platforms.size > 0}
                      filterTitle="Фильтр по системе"
                      filterContent={
                        <CheckboxListFilter
                          options={platformOptions}
                          selected={tableFilters.platforms}
                          onSelectedChange={(platforms) =>
                            setTableFilters((current) => ({ ...current, platforms }))
                          }
                        />
                      }
                      filterFooter={
                        tableFilters.platforms.size > 0 ? (
                          <button
                            type="button"
                            className="btn secondary btn-sm"
                            onClick={() =>
                              setTableFilters((current) => ({ ...current, platforms: new Set() }))
                            }
                          >
                            Сбросить
                          </button>
                        ) : undefined
                      }
                    />
                    <ColumnHeader
                      label="Посещение"
                      sortActive={sortKey === "visited"}
                      sortAsc={sortAsc}
                      onSort={() => handleSort("visited")}
                      filterActive={tableFilters.visited !== "all"}
                      filterContent={
                        <VisitedFilterPopover
                          value={tableFilters.visited}
                          onChange={setVisitedFilter}
                        />
                      }
                      filterFooter={
                        tableFilters.visited !== "all" ? (
                          <button
                            type="button"
                            className="btn secondary btn-sm"
                            onClick={() => setVisitedFilter("all")}
                          >
                            Сбросить
                          </button>
                        ) : undefined
                      }
                    />
                    <ColumnHeader
                      label="1-й визит"
                      sortActive={sortKey === "first_visit"}
                      sortAsc={sortAsc}
                      onSort={() => handleSort("first_visit")}
                      filterable={false}
                    />
                  </tr>
                </thead>
                <tbody>
                  {filteredRows.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="table-empty-cell">
                        <span className="muted">
                          Нет локаций для выбранных фильтров. Измените режим карты, системы или
                          фильтры столбцов.
                        </span>
                        {filtersActive && (
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm table-empty-reset"
                            onClick={() => setTableFilters(resetCatalogTableFilters())}
                          >
                            Сбросить
                          </button>
                        )}
                      </td>
                    </tr>
                  ) : (
                    filteredRows.map((row) => (
                      <CatalogLocationTableRowView key={row.row_key} row={row} />
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </details>
  );
}

function CatalogLocationTableRowView({ row }: { row: CatalogLocationTableRow }) {
  const name = row.location_slug ? (
    <a href={`/locations/${encodeURIComponent(row.location_slug)}`} className="map-popup-link" title="Открыть страницу локации">
      {row.name}
    </a>
  ) : row.location_url != null && row.location_url !== "" ? (
    <a href={row.location_url} target="_blank" rel="noreferrer noopener" className="map-popup-link">
      {row.name}
    </a>
  ) : (
    row.name
  );

  const statusNote = row.is_cancelled ? "Отменена" : row.is_paused ? "На паузе" : null;

  return (
    <tr
      className={
        row.is_cancelled
          ? "map-locations-table-row-cancelled"
          : row.is_paused
            ? "map-locations-table-row-paused"
            : undefined
      }
    >
      <td className="td-location">
        <span className="locations-table-name">{name}</span>
        {statusNote != null && (
          <span className="locations-table-status">{statusNote}</span>
        )}
        {!row.has_coordinates && (
          <span className="muted block locations-table-meta">без координат</span>
        )}
      </td>
      <td className="td-city">{row.city ?? "—"}</td>
      <td className="td-region">{row.region ?? "—"}</td>
      <td className="td-platform">
        <PlatformBadge code={row.platform_code} />
      </td>
      <td className="td-visited">
        <span
          className={
            row.visited ? "visit-status-badge visit-status-yes" : "visit-status-badge visit-status-no"
          }
        >
          {row.visited ? "Посещал" : "Не посещал"}
        </span>
      </td>
      <td className="td-date">
        {row.first_visit_date ? formatDate(row.first_visit_date) : "—"}
        {/* Отметка «Посещал» ставится по локации, а не по системе. Если первый
            визит был в другой системе (обычно parkrun-эпоха), называем её —
            иначе дата 2020 года на строке 5 вёрст выглядит ошибкой. */}
        {row.first_visit_date && row.first_visit_platform && row.first_visit_platform !== row.platform_code && (
          <span className="td-date-source">в {platformCodeLabel(row.first_visit_platform)}</span>
        )}
      </td>
    </tr>
  );
}
