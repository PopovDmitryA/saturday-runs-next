import { useCallback, useEffect, useMemo, useState } from "react";
import { LocationMap } from "../../components/LocationMap";
import type { CatalogLocationsTableResponse, MapLocationsResponse } from "../../lib/api";
import type { MapViewportRef } from "../../lib/mapViewport";
import { pluralizeRu } from "../../lib/format";
import { CatalogLocationsTable } from "./CatalogLocationsTable";
import { MapModeToggles } from "./MapFilterBar";
import {
  buildVisitedByIdentity,
  DEFAULT_PLATFORM_FILTERS,
  excludeVisitedPoints,
  filterPointsByActivity,
  filterPointsByPlatform,
  hasActivePlatformFilter,
  toggleMapMode,
  type ActivityFilter,
  type MapMode,
  type PlatformFilters,
} from "./mapFilters";

export type UserMapPanelProps = {
  loadVisitedMap: () => Promise<MapLocationsResponse>;
  loadCatalogMap: () => Promise<MapLocationsResponse>;
  loadCatalogTable?: () => Promise<CatalogLocationsTableResponse>;
  visitedTabLabel?: string;
  active?: boolean;
  viewportRef?: MapViewportRef;
  // Если переданы — фильтры контролирует родитель (общий фильтр-бар), и панель
  // не рисует свои группы «активность/системы». Иначе — свои внутренние.
  activityFilter?: ActivityFilter;
  platformFilters?: PlatformFilters;
  // Аналогично для тумблеров «Где не был»/«Мои визиты»: при переданных пропсах
  // тулбар панели не рисуется вовсе — фильтры показывает родитель.
  mapMode?: MapMode;
  onMapModeChange?: (mode: MapMode) => void;
  // Fullscreen тоже может контролировать родитель (общий полноэкранный блок
  // с фильтр-баром на /maps); без пропсов — свой внутренний, как раньше.
  isFullscreen?: boolean;
  onToggleFullscreen?: () => void;
  /**
   * Спрашивать геопозицию и показывать кнопку «где я». Включаем там, где карту
   * смотрят про себя (свой кабинет); на чужом профиле она ни к чему.
   */
  myLocation?: boolean;
};

// parkrun последний: своих локаций на карте у него нет (ушёл из России в 2022),
// переключатель управляет только тем, засчитывать ли визиты той эпохи —
// поэтому и подпись отдельная.
const PLATFORM_DEFS = [
  { code: "five_verst" as const, label: "5 вёрст", dot: "map-legend-dot-five-verst", title: undefined },
  { code: "s95" as const, label: "S95", dot: "map-legend-dot-s95", title: undefined },
  { code: "runpark" as const, label: "Runpark", dot: "map-legend-dot-runpark", title: undefined },
  {
    code: "parkrun" as const,
    label: "parkrun",
    dot: "map-legend-dot-parkrun",
    title: "Локации parkrun-эпохи: закрыты с 2022 года",
  },
];

export function UserMapPanel({
  active: isActive = true,
  viewportRef,
  loadVisitedMap,
  loadCatalogMap,
  loadCatalogTable,
  visitedTabLabel = "Мои визиты",
  activityFilter: activityFilterProp,
  platformFilters: platformFiltersProp,
  mapMode: mapModeProp,
  onMapModeChange,
  isFullscreen: isFullscreenProp,
  onToggleFullscreen,
  myLocation = false,
}: UserMapPanelProps) {
  // Контролируемый режим (общий фильтр-бар) vs автономный (admin/public-profile).
  const controlled = activityFilterProp !== undefined && platformFiltersProp !== undefined;
  const modeControlled = mapModeProp !== undefined && onMapModeChange !== undefined;
  const [modeInternal, setModeInternal] = useState<MapMode>("all");
  const mode = mapModeProp ?? modeInternal;
  const setMode = modeControlled ? onMapModeChange : setModeInternal;
  const [activityInternal, setActivityInternal] = useState<ActivityFilter>("runs");
  const [platformsInternal, setPlatformsInternal] =
    useState<PlatformFilters>(DEFAULT_PLATFORM_FILTERS);
  const activityFilter = activityFilterProp ?? activityInternal;
  const platformFilters = platformFiltersProp ?? platformsInternal;
  const fullscreenControlled = isFullscreenProp !== undefined && onToggleFullscreen !== undefined;
  const [fullscreenInternal, setFullscreenInternal] = useState(false);
  const isFullscreen = isFullscreenProp ?? fullscreenInternal;
  const toggleFullscreenInternal = useCallback(() => setFullscreenInternal((v) => !v), []);
  const toggleFullscreen = onToggleFullscreen ?? toggleFullscreenInternal;

  useEffect(() => {
    // Escape обрабатывает владелец состояния; при контролируемом fullscreen — родитель.
    if (fullscreenControlled || !isFullscreen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFullscreenInternal(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [fullscreenControlled, isFullscreen]);
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

  // Визиты, попадающие под выбранные системы. От них же считаем «посещено» для
  // карты: локацию, где человек бегал только на parkrun (система по умолчанию
  // выключена), под фильтром «5 вёрст» показываем как непосещённую. Иначе карта
  // расходилась бы с таблицей каталога, где отметка визита — по системам: там
  // строка «5 вёрст Бутово» честно «Не посещал», если визит был лишь на parkrun.
  const filteredVisitedOnMap = useMemo(
    () => filterPointsByPlatform(visitedActivityPoints, platformFilters),
    [platformFilters, visitedActivityPoints],
  );

  const visitedActivityByIdentity = useMemo(
    () => buildVisitedByIdentity(filteredVisitedOnMap),
    [filteredVisitedOnMap],
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

  const togglePlatformFilter = (code: keyof PlatformFilters) => {
    setPlatformsInternal((current) => {
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
        Не действует
      </span>
      <span className="map-legend-item">
        <span className="map-legend-dot map-legend-dot-cancelled" aria-hidden />
        Отменена
      </span>
    </>
  );

  return (
    <div className={!fullscreenControlled && isFullscreen ? "map-panel-fullscreen" : undefined}>
      {!modeControlled && (
      <div className="map-toolbar">
        {!controlled && (
          <>
            <div className="map-toolbar-group" role="tablist" aria-label="Активность">
              <button
                type="button"
                role="tab"
                aria-selected={activityFilter === "runs"}
                className={activityFilter === "runs" ? "map-mode-tab active" : "map-mode-tab"}
                onClick={() => setActivityInternal("runs")}
              >
                Пробежки
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={activityFilter === "volunteering"}
                className={activityFilter === "volunteering" ? "map-mode-tab active" : "map-mode-tab"}
                onClick={() => setActivityInternal("volunteering")}
              >
                Волонтёрство
              </button>
            </div>
            <span className="map-toolbar-sep" aria-hidden />
          </>
        )}
        <MapModeToggles
          mode={mode}
          onToggle={(which) => setMode(toggleMapMode(mode, which))}
          visitedLabel={visitedTabLabel}
        />
        {!controlled && (
          <>
            <span className="map-toolbar-sep" aria-hidden />
            <div className="map-toolbar-group" role="group" aria-label="Системы">
              {PLATFORM_DEFS.map(({ code, label, dot, title }) => (
                <button
                  key={code}
                  type="button"
                  className={
                    platformFilters[code]
                      ? "map-platform-filter-btn active"
                      : "map-platform-filter-btn"
                  }
                  onClick={() => togglePlatformFilter(code)}
                  aria-pressed={platformFilters[code]}
            title={title}
                >
                  <span className={`map-legend-dot ${dot}`} aria-hidden />
                  {label}
                </button>
              ))}
            </div>
          </>
        )}
      </div>
      )}

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
            active={isActive}
            viewportRef={viewportRef}
            myLocation={myLocation}
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
