import { useCallback, useEffect, useRef, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { RegionChoropleth } from "../../components/RegionChoropleth";
import { useAppDataSource } from "../../lib/appDataSource";
import type { CatalogLocationsTableResponse, UniqueLocationsDetailResponse } from "../../lib/api";
import type { MapViewport, MapViewportRef } from "../../lib/mapViewport";
import { UserMapPanel } from "./UserMapPanel";
import { MapFilterBar, togglePlatform } from "./MapFilterBar";
import {
  DEFAULT_PLATFORM_FILTERS,
  toggleMapMode,
  type ActivityFilter,
  type MapMode,
  type MapModeToggle,
  type MapView,
  type PlatformFilters,
} from "./mapFilters";

function RegionsPanel({
  active,
  viewportRef,
  activityFilter,
  platformFilters,
  isFullscreen,
  onToggleFullscreen,
}: {
  active: boolean;
  viewportRef: MapViewportRef;
  activityFilter: ActivityFilter;
  platformFilters: PlatformFilters;
  isFullscreen: boolean;
  onToggleFullscreen: () => void;
}) {
  const { getUniqueLocationsDetail, getCatalogLocationsTable } = useAppDataSource();
  const [detail, setDetail] = useState<UniqueLocationsDetailResponse | null>(null);
  const [catalog, setCatalog] = useState<CatalogLocationsTableResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([getUniqueLocationsDetail(false), getCatalogLocationsTable(false)])
      .then(([detailData, catalogData]) => {
        if (!cancelled) {
          setDetail(detailData);
          setCatalog(catalogData);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError("Не удалось загрузить данные регионов");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [getUniqueLocationsDetail, getCatalogLocationsTable]);

  return (
    <>
      {loading && <p className="muted">Загрузка карты…</p>}
      {error && <p className="error-text">{error}</p>}
      {detail && catalog && (
        <RegionChoropleth
          catalogRows={catalog.rows}
          detail={detail}
          mode={activityFilter}
          platformFilters={platformFilters}
          active={active}
          viewportRef={viewportRef}
          isFullscreen={isFullscreen}
          onToggleFullscreen={onToggleFullscreen}
        />
      )}
    </>
  );
}

// bare — отдать только тело страницы, без AppShell: портальный ЛК (/new/*)
// оборачивает контент в собственный каркас с сайдбаром.
function MapsContent({ bare = false }: { bare?: boolean } = {}) {
  const { getVisitedLocationsMap, getCatalogLocationsMap, getCatalogLocationsTable } =
    useAppDataSource();
  const [view, setView] = useState<MapView>("locations");
  // Общий фильтр для обеих карт: активность + режим + системы.
  const [activityFilter, setActivityFilter] = useState<ActivityFilter>("runs");
  const [mapMode, setMapMode] = useState<MapMode>("all");
  const [platformFilters, setPlatformFilters] = useState<PlatformFilters>(DEFAULT_PLATFORM_FILTERS);
  // Общий вьюпорт двух карт + держим обе панели смонтированными (скрываем
  // неактивную), чтобы переключение было мгновенным.
  const viewportRef = useRef<MapViewport | null>(null);

  const loadVisitedMap = useCallback(() => getVisitedLocationsMap(false), [getVisitedLocationsMap]);
  const loadCatalogTable = useCallback(
    () => getCatalogLocationsTable(false),
    [getCatalogLocationsTable],
  );
  const onTogglePlatform = useCallback(
    (code: keyof PlatformFilters) => setPlatformFilters((cur) => togglePlatform(cur, code)),
    [],
  );
  const onToggleMode = useCallback(
    (which: MapModeToggle) => setMapMode((cur) => toggleMapMode(cur, which)),
    [],
  );

  // Fullscreen общий для страницы: в полный экран уходит весь блок с фильтр-баром,
  // так что внутри можно переключать и вид, и все фильтры.
  const [isFullscreen, setIsFullscreen] = useState(false);
  const toggleFullscreen = useCallback(() => setIsFullscreen((v) => !v), []);
  useEffect(() => {
    if (!isFullscreen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setIsFullscreen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isFullscreen]);

  const body = (
    <div className={isFullscreen ? "map-panel-fullscreen" : undefined}>
      <MapFilterBar
        view={view}
        onViewChange={setView}
        activityFilter={activityFilter}
        onActivityChange={setActivityFilter}
        mapMode={mapMode}
        onToggleMode={onToggleMode}
        platformFilters={platformFilters}
        onTogglePlatform={onTogglePlatform}
      />

      <div className="maps-view-panel" hidden={view !== "locations"}>
        <UserMapPanel
          active={view === "locations"}
          viewportRef={viewportRef}
          activityFilter={activityFilter}
          platformFilters={platformFilters}
          mapMode={mapMode}
          onMapModeChange={setMapMode}
          isFullscreen={isFullscreen}
          onToggleFullscreen={toggleFullscreen}
          loadVisitedMap={loadVisitedMap}
          loadCatalogMap={getCatalogLocationsMap}
          loadCatalogTable={loadCatalogTable}
          myLocation
        />
      </div>
      <div className="maps-view-panel" hidden={view !== "regions"}>
        <RegionsPanel
          active={view === "regions"}
          viewportRef={viewportRef}
          activityFilter={activityFilter}
          platformFilters={platformFilters}
          isFullscreen={isFullscreen}
          onToggleFullscreen={toggleFullscreen}
        />
      </div>
    </div>
  );

  if (bare) {
    return body;
  }

  return <AppShell title="Карта">{body}</AppShell>;
}

export { MapsContent };

