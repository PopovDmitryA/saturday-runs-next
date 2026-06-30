import { useCallback, useEffect, useMemo, useState } from "react";
import { LocationMap } from "../../components/LocationMap";
import type { CatalogLocationsTableResponse, MapLocationsResponse } from "../../lib/api";
import { pluralizeRu } from "../../lib/format";
import { CatalogLocationsTable } from "./CatalogLocationsTable";
import {
  buildVisitedByIdentity,
  DEFAULT_PLATFORM_FILTERS,
  excludeVisitedPoints,
  filterPointsByActivity,
  filterPointsByPlatform,
  hasActivePlatformFilter,
  type ActivityFilter,
  type PlatformFilters,
} from "./mapFilters";

type MapMode = "all" | "unvisited" | "visited";

export type UserMapPanelProps = {
  loadVisitedMap: () => Promise<MapLocationsResponse>;
  loadCatalogMap: () => Promise<MapLocationsResponse>;
  loadCatalogTable?: () => Promise<CatalogLocationsTableResponse>;
  visitedTabLabel?: string;
};

const MODE_DEFS: { mode: MapMode; label: string }[] = [
  { mode: "all", label: "Все площадки" },
  { mode: "unvisited", label: "Где не был" },
  { mode: "visited", label: "Мои визиты" },
];

const PLATFORM_DEFS = [
  { code: "five_verst" as const, label: "5 вёрст", dot: "map-legend-dot-five-verst" },
  { code: "s95" as const, label: "S95", dot: "map-legend-dot-s95" },
  { code: "runpark" as const, label: "Runpark", dot: "map-legend-dot-runpark" },
];

export function UserMapPanel({
  loadVisitedMap,
  loadCatalogMap,
  loadCatalogTable,
  visitedTabLabel = "Мои визиты",
}: UserMapPanelProps) {
  const [mode, setMode] = useState<MapMode>("all");
  const [activityFilter, setActivityFilter] = useState<ActivityFilter>("runs");
  const [platformFilters, setPlatformFilters] = useState<PlatformFilters>(DEFAULT_PLATFORM_FILTERS);
  const [isFullscreen, setIsFullscreen] = useState(false);

  const toggleFullscreen = useCallback(() => setIsFullscreen((v) => !v), []);

  useEffect(() => {
    if (!isFullscreen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setIsFullscreen(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isFullscreen]);
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

  const isCatalogMode = mode === "all" || mode === "unvisited";

  const visitedActivityPoints = useMemo(
    () => filterPointsByActivity(visited?.points ?? [], activityFilter),
    [visited?.points, activityFilter],
  );

  const visitedActivityByIdentity = useMemo(
    () => buildVisitedByIdentity(visitedActivityPoints),
    [visitedActivityPoints],
  );

  const sourcePoints = mode === "visited" ? visitedActivityPoints : catalog?.points ?? [];
  const filteredPoints = useMemo(() => {
    let points = filterPointsByPlatform(sourcePoints, platformFilters, {
      onlyMapPlatforms: isCatalogMode,
    });
    if (mode === "unvisited") {
      points = excludeVisitedPoints(points, visitedActivityByIdentity);
    }
    return points;
  }, [isCatalogMode, mode, platformFilters, sourcePoints, visitedActivityByIdentity]);

  const filteredVisitedOnMap = useMemo(
    () => filterPointsByPlatform(visitedActivityPoints, platformFilters),
    [platformFilters, visitedActivityPoints],
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

  const mapLegend = (
    <>
      {mode === "all" && (
        <span className="map-legend-item">
          <span className="map-legend-dot map-legend-dot-visited" aria-hidden />
          Посещено
        </span>
      )}
      {mode === "visited" && (
        <span className="map-legend-item">
          <span className="map-legend-dot map-legend-dot-visited" aria-hidden />
          Активные
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
    </>
  );

  return (
    <div className={isFullscreen ? "map-panel-fullscreen" : undefined}>
      <div className="map-toolbar">
        <div className="map-toolbar-group" role="tablist" aria-label="Активность">
          <button
            type="button"
            role="tab"
            aria-selected={activityFilter === "runs"}
            className={activityFilter === "runs" ? "map-mode-tab active" : "map-mode-tab"}
            onClick={() => setActivityFilter("runs")}
          >
            Пробежки
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activityFilter === "volunteering"}
            className={activityFilter === "volunteering" ? "map-mode-tab active" : "map-mode-tab"}
            onClick={() => setActivityFilter("volunteering")}
          >
            Волонтёрство
          </button>
        </div>
        <span className="map-toolbar-sep" aria-hidden />
        <div className="map-toolbar-group" role="tablist" aria-label="Режим карты">
          {MODE_DEFS.map(({ mode: m, label }) => (
            <button
              key={m}
              type="button"
              role="tab"
              aria-selected={mode === m}
              className={mode === m ? "map-mode-tab active" : "map-mode-tab"}
              onClick={() => setMode(m)}
            >
              {m === "visited" ? visitedTabLabel : label}
            </button>
          ))}
        </div>
        <span className="map-toolbar-sep" aria-hidden />
        <div className="map-toolbar-group" role="group" aria-label="Системы">
          {PLATFORM_DEFS.map(({ code, label, dot }) => (
            <button
              key={code}
              type="button"
              className={
                platformFilters[code] ? "map-platform-filter-btn active" : "map-platform-filter-btn"
              }
              onClick={() => togglePlatformFilter(code)}
              aria-pressed={platformFilters[code]}
            >
              <span className={`map-legend-dot ${dot}`} aria-hidden />
              {label}
            </button>
          ))}
        </div>
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
          <p className="map-stats-line muted">
            {isCatalogMode ? (
              <>
                {pluralizeRu(filtersActive ? filteredPoints.length : 0, [
                  "площадка",
                  "площадки",
                  "площадок",
                ])}{" "}
                на карте
                {mode === "all" && filtersActive && visitedActivityPoints.length > 0 && (
                  <> · уникальных локаций {visitedActivityPoints.length}</>
                )}
                {mode === "unvisited" && filtersActive && (
                  <> · {activityFilter === "runs" ? "без пробежек" : "без волонтёрства"}</>
                )}
              </>
            ) : (
              <>
                {pluralizeRu(filtersActive ? filteredVisitedOnMap.length : 0, [
                  "локация",
                  "локации",
                  "локаций",
                ])}{" "}
                на карте
                {visitedActivityPoints.length > 0 && (
                  <>
                    {" · "}уникальных локаций {visitedActivityPoints.length}
                    {(() => {
                      const unmapped = visitedActivityPoints.filter((p) => !p.latitude).length;
                      return unmapped > 0 ? ` (${unmapped} не на карте)` : null;
                    })()}
                  </>
                )}
              </>
            )}
          </p>

          {mode === "visited" && visitedActivityPoints.length === 0 && (
            <p className="muted">
              {activityFilter === "runs"
                ? "Пока нет пробежек с привязанными локациями."
                : "Пока нет волонтёрств с привязанными локациями."}
              {" "}Переключитесь на «Все площадки» или «Где не был», чтобы увидеть полный список.
            </p>
          )}
          {!filtersActive && (
            <p className="muted">Выберите хотя бы одну систему, чтобы показать локации на карте.</p>
          )}

          <LocationMap
            points={filtersActive ? filteredPoints : []}
            variant={isCatalogMode ? "catalog" : "visited"}
            visitedByIdentity={mode === "all" ? visitedActivityByIdentity : undefined}
            legend={mapLegend}
            isFullscreen={isFullscreen}
            onToggleFullscreen={toggleFullscreen}
            emptyMessage={
              !filtersActive
                ? "Выберите хотя бы одну систему."
                : mode === "visited"
                  ? activityFilter === "runs"
                    ? "Нет локаций пробежек с координатами для выбранных систем."
                    : "Нет локаций волонтёрств с координатами для выбранных систем."
                  : mode === "unvisited"
                    ? activityFilter === "runs"
                      ? "Нет площадок без пробежек с координатами для выбранных систем."
                      : "Нет площадок без волонтёрства с координатами для выбранных систем."
                    : "Нет локаций с координатами для выбранных систем."
            }
          />
          {loadCatalogTable && (
            <CatalogLocationsTable
              data={catalogTable}
              loading={catalogTableLoading || loading}
              error={catalogTableError}
              platformFilters={platformFilters}
            />
          )}
        </>
      )}
    </div>
  );
}
