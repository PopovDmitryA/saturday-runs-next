import { useCallback, useEffect, useMemo, useState } from "react";
import { LocationMap } from "../../components/LocationMap";
import type { CatalogLocationsTableResponse, MapLocationsResponse } from "../../lib/api";
import { pluralizeRu, uniqueLocationsTitle } from "../../lib/format";
import { CatalogLocationsTable } from "./CatalogLocationsTable";
import {
  buildVisitedByIdentity,
  countVisitedMatchingCatalogPoints,
  DEFAULT_PLATFORM_FILTERS,
  excludeVisitedPoints,
  filterPointsByPlatform,
  hasActivePlatformFilter,
  type PlatformFilters,
} from "./mapFilters";

type MapMode = "all" | "unvisited" | "visited";

export type UserMapPanelProps = {
  loadVisitedMap: () => Promise<MapLocationsResponse>;
  loadCatalogMap: () => Promise<MapLocationsResponse>;
  loadCatalogTable?: () => Promise<CatalogLocationsTableResponse>;
  visitedTabLabel?: string;
};

export function UserMapPanel({
  loadVisitedMap,
  loadCatalogMap,
  loadCatalogTable,
  visitedTabLabel = "Мои визиты",
}: UserMapPanelProps) {
  const [mode, setMode] = useState<MapMode>("all");
  const [platformFilters, setPlatformFilters] = useState<PlatformFilters>(DEFAULT_PLATFORM_FILTERS);
  const [visited, setVisited] = useState<MapLocationsResponse | null>(null);
  const [catalog, setCatalog] = useState<MapLocationsResponse | null>(null);
  const [catalogTable, setCatalogTable] = useState<CatalogLocationsTableResponse | null>(null);
  const [catalogTableError, setCatalogTableError] = useState<string | null>(null);
  const [catalogTableLoading, setCatalogTableLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setCatalogTableLoading(Boolean(loadCatalogTable));
    setError(null);
    setCatalogTableError(null);
    try {
      const [visitedData, catalogData, tableData] = await Promise.all([
        loadVisitedMap(),
        loadCatalogMap(),
        loadCatalogTable ? loadCatalogTable() : Promise.resolve(null),
      ]);
      setVisited(visitedData);
      setCatalog(catalogData);
      if (tableData) {
        setCatalogTable(tableData);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить карту");
    } finally {
      setLoading(false);
      setCatalogTableLoading(false);
    }
  }, [loadCatalogMap, loadCatalogTable, loadVisitedMap]);

  useEffect(() => {
    void load();
  }, [load]);

  const visitedByIdentity = useMemo(
    () => buildVisitedByIdentity(visited?.points ?? []),
    [visited?.points],
  );

  const isCatalogMode = mode === "all" || mode === "unvisited";
  const sourcePoints = mode === "visited" ? visited?.points ?? [] : catalog?.points ?? [];
  const filteredPoints = useMemo(() => {
    let points = filterPointsByPlatform(sourcePoints, platformFilters, {
      onlyMapPlatforms: isCatalogMode,
    });
    if (mode === "unvisited") {
      points = excludeVisitedPoints(points, visitedByIdentity);
    }
    return points;
  }, [isCatalogMode, mode, platformFilters, sourcePoints, visitedByIdentity]);

  const filteredVisitedOnMap = useMemo(
    () => filterPointsByPlatform(visited?.points ?? [], platformFilters),
    [platformFilters, visited?.points],
  );

  const filteredVisitedOnCatalog = useMemo(
    () => countVisitedMatchingCatalogPoints(filteredPoints, visitedByIdentity),
    [filteredPoints, visitedByIdentity],
  );

  const togglePlatformFilter = (code: keyof PlatformFilters) => {
    setPlatformFilters((current) => {
      const next = { ...current, [code]: !current[code] };
      if (!hasActivePlatformFilter(next)) {
        return current;
      }
      return next;
    });
  };

  const filtersActive = hasActivePlatformFilter(platformFilters);
  const active = isCatalogMode ? catalog : visited;
  const uniqueTotal = visited?.total_locations ?? 0;
  const uniqueOnMap = visited?.mapped_locations ?? 0;
  const uniqueWithoutCoords = visited?.unmapped_locations ?? 0;

  return (
    <>
      <div className="map-mode-tabs" role="tablist" aria-label="Режим карты">
        <button
          type="button"
          role="tab"
          aria-selected={mode === "all"}
          className={mode === "all" ? "map-mode-tab active" : "map-mode-tab"}
          onClick={() => setMode("all")}
        >
          Все площадки
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "unvisited"}
          className={mode === "unvisited" ? "map-mode-tab active" : "map-mode-tab"}
          onClick={() => setMode("unvisited")}
        >
          Где не был
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "visited"}
          className={mode === "visited" ? "map-mode-tab active" : "map-mode-tab"}
          onClick={() => setMode("visited")}
        >
          {visitedTabLabel}
        </button>
      </div>

      <div className="map-platform-filters" role="group" aria-label="Фильтр по системам">
        <span className="map-platform-filters-label">Системы:</span>
        <label className="map-platform-filter">
          <input
            type="checkbox"
            checked={platformFilters.five_verst}
            onChange={() => togglePlatformFilter("five_verst")}
          />
          <span className="map-legend-dot map-legend-dot-five-verst" aria-hidden />
          5 вёрст
        </label>
        <label className="map-platform-filter">
          <input
            type="checkbox"
            checked={platformFilters.s95}
            onChange={() => togglePlatformFilter("s95")}
          />
          <span className="map-legend-dot map-legend-dot-s95" aria-hidden />
          S95
        </label>
        <label className="map-platform-filter">
          <input
            type="checkbox"
            checked={platformFilters.runpark}
            onChange={() => togglePlatformFilter("runpark")}
          />
          <span className="map-legend-dot map-legend-dot-runpark" aria-hidden />
          Runpark
        </label>
      </div>

      {loading && !active && <p className="muted">Загрузка карты…</p>}

      {error && (
        <div className="card error">
          <p>{error}</p>
          <button type="button" className="btn secondary" onClick={() => void load()}>
            Повторить
          </button>
        </div>
      )}

      {!loading && !error && active && (
        <>
          <div className="card map-stats-card">
            {uniqueTotal > 0 && (
              <p>
                {uniqueLocationsTitle(uniqueTotal)}: <strong>{uniqueTotal}</strong>
                <span className="muted">
                  {" "}
                  · на карте: {filtersActive ? filteredVisitedOnMap.length : uniqueOnMap}
                  {uniqueWithoutCoords > 0 &&
                    ` · ${pluralizeRu(uniqueWithoutCoords, ["локация", "локации", "локаций"])} без координат`}
                </span>
              </p>
            )}
            <p>
              {isCatalogMode ? (
                <>
                  <strong>
                    {pluralizeRu(filtersActive ? filteredPoints.length : 0, [
                      "Площадка",
                      "Площадки",
                      "Площадок",
                    ])}
                  </strong>{" "}
                  на карте
                  {mode === "all" && filtersActive && filteredPoints.length > 0 && (
                    <span className="muted"> (визиты среди них: {filteredVisitedOnCatalog})</span>
                  )}
                  {mode === "unvisited" && filtersActive && (
                    <span className="muted"> (только площадки без пробежек и волонтёрств)</span>
                  )}
                </>
              ) : (
                <>
                  <strong>
                    {pluralizeRu(filtersActive ? filteredPoints.length : 0, [
                      "Маркер",
                      "Маркера",
                      "Маркеров",
                    ])}
                  </strong>{" "}
                  на карте
                </>
              )}
            </p>
            {mode === "visited" && active.total_locations === 0 && (
              <p className="muted">
                Пока нет пробежек или волонтёрств с привязанными локациями. Переключитесь на «Все
                площадки» или «Где не был», чтобы увидеть полный список.
              </p>
            )}
            {isCatalogMode && (
              <>
                <div className="map-legend" aria-label="Легенда карты">
                  <span className="map-legend-item">
                    <span className="map-legend-dot map-legend-dot-five-verst" aria-hidden />
                    5 вёрст
                  </span>
                  <span className="map-legend-item">
                    <span className="map-legend-dot map-legend-dot-s95" aria-hidden />
                    S95
                  </span>
                  <span className="map-legend-item">
                    <span className="map-legend-dot map-legend-dot-runpark" aria-hidden />
                    Runpark
                  </span>
                  {mode === "all" && (
                    <span className="map-legend-item">
                      <span className="map-legend-dot map-legend-dot-visited" aria-hidden />
                      Посещено
                    </span>
                  )}
                  <span className="map-legend-item">
                    <span className="map-legend-dot map-legend-dot-paused" aria-hidden />
                    На паузе
                  </span>
                  <span className="map-legend-item">
                    <span className="map-legend-dot map-legend-dot-cancelled" aria-hidden />
                    Отменена
                  </span>
                </div>
              </>
            )}
            {mode === "visited" && (
              <div className="map-legend" aria-label="Легенда карты">
                <span className="map-legend-item">
                  <span className="map-legend-dot map-legend-dot-visited" aria-hidden />
                  Активные
                </span>
                <span className="map-legend-item">
                  <span className="map-legend-dot map-legend-dot-paused" aria-hidden />
                  На паузе
                </span>
                <span className="map-legend-item">
                  <span className="map-legend-dot map-legend-dot-cancelled" aria-hidden />
                  Отменена
                </span>
              </div>
            )}
            {!filtersActive && (
              <p className="muted">Выберите хотя бы одну систему, чтобы показать локации на карте.</p>
            )}
          </div>

          <LocationMap
            points={filtersActive ? filteredPoints : []}
            variant={isCatalogMode ? "catalog" : "visited"}
            visitedByIdentity={mode === "all" ? visitedByIdentity : undefined}
            emptyMessage={
              !filtersActive
                ? "Выберите хотя бы одну систему."
                : mode === "visited"
                  ? "Нет посещённых локаций с координатами для выбранных систем."
                  : mode === "unvisited"
                    ? "Нет непосещённых площадок с координатами для выбранных систем."
                    : "Нет локаций с координатами для выбранных систем."
            }
          />
          {loadCatalogTable && (
            <CatalogLocationsTable
              data={catalogTable}
              loading={catalogTableLoading || loading}
              error={catalogTableError}
              mapMode={mode}
              platformFilters={platformFilters}
            />
          )}
        </>
      )}
    </>
  );
}
