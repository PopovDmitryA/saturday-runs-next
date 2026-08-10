import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { CatalogLocationTableRow, UniqueLocationsDetailResponse } from "../lib/api";
import { regionKey } from "../lib/regionMatch";
import { pluralFormRu, pluralizeRu } from "../lib/format";
import type { MapViewportRef } from "../lib/mapViewport";
import { addZoomControl } from "../lib/mapZoomControl";
import type { PlatformFilters } from "../features/maps/mapFilters";
import { MapFullscreenButton } from "./MapFullscreenButton";

export type RegionMode = "runs" | "volunteering";

// Системы, которые показываем на карте (parkrun сюда не входит).
const MAP_PLATFORMS = new Set(["five_verst", "s95", "runpark"]);

type RegionChoroplethProps = {
  catalogRows: CatalogLocationTableRow[];
  detail: UniqueLocationsDetailResponse;
  mode: RegionMode;
  platformFilters: PlatformFilters;
  active?: boolean;
  viewportRef?: MapViewportRef;
  isFullscreen?: boolean;
  onToggleFullscreen?: () => void;
};

type Coverage = {
  displayName: string;
  available: number;
  visited: number;
  pct: number;
};

type FeatureCollection = {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    properties: { name: string };
    geometry: GeoJSON.Geometry;
  }>;
};

const REGION_FORMS = ["регион", "региона", "регионов"] as const;

type Ramp = { from: [number, number, number]; to: [number, number, number] };

// Россия — по режиму (зелёный/синий). Зарубежные страны — оранжевый, чтобы
// визуально отличались от регионов РФ.
const RAMP: Record<RegionMode, Ramp> = {
  runs: { from: [220, 245, 228], to: [20, 83, 45] }, // бледно-зелёный → тёмно-зелёный
  volunteering: { from: [219, 234, 254], to: [30, 58, 138] }, // бледно-синий → тёмно-синий
};
const COUNTRY_RAMP: Ramp = { from: [255, 237, 213], to: [194, 65, 12] }; // бледно → тёмно-оранжевый

const COLOR_IMPOSSIBLE = "#e5e9ef";

function lerp(a: number, b: number, t: number): number {
  return Math.round(a + (b - a) * t);
}

function coverageColor(pct: number, ramp: Ramp): string {
  const { from, to } = ramp;
  const t = 0.12 + (pct / 100) * 0.88;
  return `rgb(${lerp(from[0], to[0], t)}, ${lerp(from[1], to[1], t)}, ${lerp(from[2], to[2], t)})`;
}

type CoverageResult = {
  regions: Map<string, Coverage>; // ключ — regionKey (Россия)
  countries: Map<string, Coverage>; // ключ — название страны (зарубеж)
};

function buildCoverage(
  catalogRows: CatalogLocationTableRow[],
  detail: UniqueLocationsDetailResponse,
  mode: RegionMode,
  platformFilters: PlatformFilters,
): CoverageResult {
  const availableByRegion = new Map<string, { name: string; ids: Set<string> }>();
  const availableByCountry = new Map<string, { name: string; ids: Set<string> }>();

  for (const row of catalogRows) {
    if (row.is_paused || row.is_cancelled) {
      continue;
    }
    // Per-system фильтр: только системы карты и только включённые.
    if (!MAP_PLATFORMS.has(row.platform_code)) {
      continue;
    }
    if (!platformFilters[row.platform_code as keyof PlatformFilters]) {
      continue;
    }
    const country = row.country;
    if (country && country !== "Россия") {
      let bucket = availableByCountry.get(country);
      if (!bucket) {
        bucket = { name: country, ids: new Set() };
        availableByCountry.set(country, bucket);
      }
      bucket.ids.add(row.catalog_identity_key);
    } else {
      const key = regionKey(row.region);
      if (!key) {
        continue;
      }
      let bucket = availableByRegion.get(key);
      if (!bucket) {
        bucket = { name: row.region ?? key, ids: new Set() };
        availableByRegion.set(key, bucket);
      }
      bucket.ids.add(row.catalog_identity_key);
    }
  }

  const visitedIds = new Set<string>();
  for (const location of detail.locations) {
    const count = mode === "runs" ? location.run_count : location.volunteer_count;
    if (count > 0) {
      visitedIds.add(location.catalog_identity_key);
    }
  }

  const toCoverage = (
    source: Map<string, { name: string; ids: Set<string> }>,
  ): Map<string, Coverage> => {
    const out = new Map<string, Coverage>();
    for (const [key, bucket] of source) {
      let visited = 0;
      for (const id of bucket.ids) {
        if (visitedIds.has(id)) {
          visited += 1;
        }
      }
      const available = bucket.ids.size;
      out.set(key, {
        displayName: bucket.name,
        available,
        visited,
        pct: available > 0 ? Math.round((visited / available) * 100) : 0,
      });
    }
    return out;
  };

  return { regions: toCoverage(availableByRegion), countries: toCoverage(availableByCountry) };
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function tooltipHtml(featureName: string, coverage: Coverage | undefined, mode: RegionMode): string {
  const title = `<strong>${escapeHtml(featureName)}</strong>`;
  if (!coverage) {
    return `<div class="map-popup">${title}<div class="map-popup-line muted">Нет доступных стартов</div></div>`;
  }
  const activityWord = mode === "runs" ? "пробежки" : "волонтёрство";
  const lines = [
    title,
    `<div class="map-popup-line">Закрыто <strong>${coverage.pct}%</strong> локаций</div>`,
    `<div class="map-popup-line muted">${coverage.visited} из ${pluralizeRu(coverage.available, ["локации", "локаций", "локаций"])} · ${activityWord}</div>`,
  ];
  return `<div class="map-popup">${lines.join("")}</div>`;
}

export function RegionChoropleth({
  catalogRows,
  detail,
  mode,
  platformFilters,
  active = true,
  viewportRef,
  isFullscreen = false,
  onToggleFullscreen,
}: RegionChoroplethProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layersRef = useRef<L.GeoJSON[]>([]);
  const labelLayerRef = useRef<L.LayerGroup | null>(null);
  const applyViewRef = useRef<(() => void) | null>(null);
  const hasFittedRef = useRef(false);
  // Актуальное значение active для обработчика moveend (он регистрируется один раз).
  const activeRef = useRef(active);
  activeRef.current = active;
  const [regionsGeo, setRegionsGeo] = useState<FeatureCollection | null>(null);
  const [countriesGeo, setCountriesGeo] = useState<FeatureCollection | null>(null);
  const [loadError, setLoadError] = useState(false);

  const coverage = useMemo(
    () => buildCoverage(catalogRows, detail, mode, platformFilters),
    [catalogRows, detail, mode, platformFilters],
  );

  const summary = useMemo(() => {
    let runnable = 0;
    let started = 0;
    let completed = 0;
    for (const area of [...coverage.regions.values(), ...coverage.countries.values()]) {
      if (area.available > 0) {
        runnable += 1;
        if (area.visited > 0) started += 1;
        if (area.pct >= 100) completed += 1;
      }
    }
    return { runnable, started, completed };
  }, [coverage]);

  useEffect(() => {
    let cancelled = false;
    const grab = (url: string, set: (v: FeatureCollection) => void) =>
      fetch(url)
        .then((r) => {
          if (!r.ok) throw new Error(`geojson ${r.status}`);
          return r.json();
        })
        .then((data: FeatureCollection) => {
          if (!cancelled) set(data);
        })
        .catch(() => {
          if (!cancelled) setLoadError(true);
        });
    void grab("/russia-regions.geojson", setRegionsGeo);
    void grab("/world-countries.geojson", setCountriesGeo);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) {
      return;
    }
    const map = L.map(containerRef.current, {
      scrollWheelZoom: true,
      attributionControl: false,
      zoomControl: false,
      // Диапазон зума как у карты площадок: общий вьюпорт не должен обрезаться
      // при переключении между картами.
      minZoom: 1,
      maxZoom: 19,
    }).setView([62, 94], 3);

    addZoomControl(map);

    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
      subdomains: "abcd",
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap &copy; CARTO",
    }).addTo(map);
    L.control.attribution({ prefix: false }).addTo(map);

    mapRef.current = map;

    // Видимая карта — источник правды общего вьюпорта: пишем его при каждом
    // moveend (включая программный первичный fit), чтобы карта площадок открывалась
    // ровно в том же месте — и в дефолтном состоянии, и после зума пользователя.
    const writeViewport = () => {
      if (!viewportRef || !activeRef.current) return;
      const center = map.getCenter();
      viewportRef.current = { lat: center.lat, lng: center.lng, zoom: map.getZoom() };
    };
    map.on("moveend", writeViewport);

    return () => {
      map.off("moveend", writeViewport);
      map.remove();
      mapRef.current = null;
      layersRef.current = [];
      labelLayerRef.current = null;
    };
  }, [viewportRef]);

  useEffect(() => {
    if (active) {
      applyViewRef.current?.();
    }
  }, [active, regionsGeo, countriesGeo]);

  // Вход/выход из fullscreen меняет размер контейнера — пересчитать и вернуть вид.
  useEffect(() => {
    if (!mapRef.current) return;
    const timer = window.setTimeout(() => applyViewRef.current?.(), 50);
    return () => window.clearTimeout(timer);
  }, [isFullscreen]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !regionsGeo || !countriesGeo) {
      return;
    }
    for (const l of layersRef.current) {
      l.remove();
    }
    layersRef.current = [];
    if (labelLayerRef.current) {
      labelLayerRef.current.remove();
    }
    const labelLayer = L.layerGroup();
    labelLayerRef.current = labelLayer;

    const visitedBounds = L.latLngBounds([]);
    const labelPoints: Array<{ center: L.LatLng; pct: number }> = [];

    // Слой: geojson + функция ключа покрытия. onlyCovered — рисуем только фичи,
    // у которых есть покрытие (для стран: не подсвечиваем страны без наших локаций).
    const buildLayer = (
      geo: FeatureCollection,
      coverageMap: Map<string, Coverage>,
      keyOf: (name: string) => string,
      onlyCovered: boolean,
      ramp: Ramp,
    ) => {
      const layer = L.geoJSON(geo as GeoJSON.FeatureCollection, {
        filter: (feature) => {
          if (!onlyCovered) return true;
          const name = (feature?.properties as { name: string } | undefined)?.name ?? "";
          return coverageMap.has(keyOf(name));
        },
        style: (feature) => {
          const name = (feature?.properties as { name: string } | undefined)?.name ?? "";
          const area = coverageMap.get(keyOf(name));
          if (!area || area.available === 0) {
            return { fillColor: COLOR_IMPOSSIBLE, fillOpacity: 0.15, color: "#94a3b8", weight: 0.6 };
          }
          return {
            fillColor: coverageColor(area.pct, ramp),
            fillOpacity: 0.78,
            color: "#ffffff",
            weight: 1,
          };
        },
        onEachFeature: (feature, featureLayer) => {
          const name = (feature.properties as { name: string }).name;
          const area = coverageMap.get(keyOf(name));
          if (area && area.visited > 0) {
            const bounds = (featureLayer as L.Polygon).getBounds();
            visitedBounds.extend(bounds);
            labelPoints.push({ center: bounds.getCenter(), pct: area.pct });
          }
          featureLayer.bindTooltip(tooltipHtml(name, area, mode), {
            sticky: true,
            direction: "top",
            opacity: 0.95,
            className: "map-marker-tooltip region-hover-tooltip",
          });
          const runnable = Boolean(area && area.available > 0);
          featureLayer.on({
            // Цвет обводки читаем в момент ховера: тема может переключиться
            // после отрисовки слоя.
            mouseover: () =>
              (featureLayer as L.Path).setStyle({
                weight: 2,
                color: document.documentElement.dataset.theme === "dark" ? "#e6ecf7" : "#0f172a",
              }),
            mouseout: () =>
              (featureLayer as L.Path).setStyle(
                runnable ? { weight: 1, color: "#ffffff" } : { weight: 0.6, color: "#94a3b8" },
              ),
          });
        },
      }).addTo(map);
      layersRef.current.push(layer);
      return layer;
    };

    const regionLayer = buildLayer(regionsGeo, coverage.regions, (n) => regionKey(n), false, RAMP[mode]);
    buildLayer(countriesGeo, coverage.countries, (n) => n, true, COUNTRY_RAMP);
    labelLayer.addTo(map);

    const MIN_GAP_PX = 30;
    const ordered = [...labelPoints].sort((a, b) => b.pct - a.pct);
    const placeLabels = () => {
      labelLayer.clearLayers();
      const placed: L.Point[] = [];
      for (const item of ordered) {
        const point = map.latLngToContainerPoint(item.center);
        if (placed.some((p) => p.distanceTo(point) < MIN_GAP_PX)) {
          continue;
        }
        placed.push(point);
        L.marker(item.center, {
          interactive: false,
          keyboard: false,
          icon: L.divIcon({ className: "region-pct-label", html: `${item.pct}%`, iconSize: [34, 14] }),
        }).addTo(labelLayer);
      }
    };
    map.on("zoomend", placeLabels);

    const applyView = () => {
      // Читаем общий вьюпорт ДО invalidateSize: тот может дёрнуть moveend уже
      // активной карты и перезаписать shared её собственным (устаревшим) видом.
      const shared = viewportRef?.current;
      map.invalidateSize();
      try {
        if (shared) {
          map.setView([shared.lat, shared.lng], shared.zoom, { animate: false });
        } else if (!hasFittedRef.current) {
          map.fitBounds(
            visitedBounds.isValid() ? visitedBounds.pad(0.35) : regionLayer.getBounds().pad(0.05),
            { animate: false },
          );
          hasFittedRef.current = true;
        }
      } catch {
        // keep default view
      }
      placeLabels();
    };
    applyViewRef.current = applyView;

    if (active) {
      applyView();
      const timer = window.setTimeout(applyView, 200);
      return () => {
        window.clearTimeout(timer);
        map.off("zoomend", placeLabels);
      };
    }
    return () => {
      map.off("zoomend", placeLabels);
    };
  }, [regionsGeo, countriesGeo, coverage, mode, active, viewportRef]);

  return (
    <div className="region-choropleth">
      {loadError && (
        <p className="error-text">Не удалось загрузить карту. Попробуйте обновить страницу.</p>
      )}
      <p className="map-stats-line muted region-choropleth-count">
        {summary.started} из {summary.runnable} {pluralFormRu(summary.runnable, REGION_FORMS)}
        /стран с визитами
        {summary.completed > 0 ? ` · ${summary.completed} на 100%` : ""}
      </p>
      <div className="location-map-shell">
        <div ref={containerRef} className="region-choropleth-map" />
        {onToggleFullscreen && (
          <MapFullscreenButton isFullscreen={isFullscreen} onToggle={onToggleFullscreen} />
        )}
      </div>
      <div className="region-choropleth-footer">
        <div className="region-choropleth-ramp">
          <span className="muted">Россия</span>
          <span
            className="region-ramp-bar"
            style={{
              background: `linear-gradient(90deg, ${coverageColor(0, RAMP[mode])}, ${coverageColor(100, RAMP[mode])})`,
            }}
          />
          <span className="region-ramp-sep" />
          <span className="muted">Страны</span>
          <span
            className="region-ramp-bar"
            style={{
              background: `linear-gradient(90deg, ${coverageColor(0, COUNTRY_RAMP)}, ${coverageColor(100, COUNTRY_RAMP)})`,
            }}
          />
          <span className="muted">0 → 100%</span>
          <span className="region-ramp-sep" />
          <span className="analytics-legend-item">
            <span className="analytics-legend-swatch region-swatch-impossible" />
            Нет стартов
          </span>
        </div>
      </div>
    </div>
  );
}
