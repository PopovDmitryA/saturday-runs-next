import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { addZoomControl } from "../../lib/mapZoomControl";
import { COUNT_FORMS, formatInt, pluralizeRu } from "../../lib/format";
import { regionKey } from "../../lib/regionMatch";
import { PortalHeader } from "./PortalHeader";
import { fetchPortalHome, type PortalGeoPoint } from "./portalTypes";
import "./portal.css";

const PLATFORM_META: Record<string, { title: string; varName: string }> = {
  five_verst: { title: "5 вёрст", varName: "--accent-green-text" },
  s95: { title: "S95", varName: "--link" },
  runpark: { title: "RunPark", varName: "--tint-orange-text" },
  parkrun: { title: "parkrun", varName: "--tint-violet-text" },
};

function cssColor(varName: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
  return value || fallback;
}

function baseMap(container: HTMLDivElement): L.Map {
  const map = L.map(container, {
    scrollWheelZoom: false,
    attributionControl: false,
    zoomControl: false,
  });
  addZoomControl(map);
  L.control.attribution({ prefix: false }).addTo(map);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  return map;
}

function fitToPoints(map: L.Map, points: PortalGeoPoint[]) {
  const bounds = L.latLngBounds(points.map((p) => L.latLng(p.latitude, p.longitude)));
  map.fitBounds(bounds, { padding: [22, 22] });
}

function useLeaflet(
  points: PortalGeoPoint[] | null,
  build: (map: L.Map, points: PortalGeoPoint[]) => void,
) {
  const ref = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  useEffect(() => {
    if (!ref.current || mapRef.current || !points || points.length === 0) {
      return;
    }
    const map = baseMap(ref.current);
    fitToPoints(map, points);
    build(map, points);
    mapRef.current = map;
    // Контейнер мог не иметь размеров в момент fitBounds (страница ещё
    // раскладывается) — пересчитываем вид, когда layout устоялся.
    const settle = window.setTimeout(() => {
      map.invalidateSize();
      fitToPoints(map, points);
    }, 300);
    return () => {
      window.clearTimeout(settle);
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points]);
  return ref;
}

/* A. Точки в цветах систем */
function VariantPlatformDots({ points }: { points: PortalGeoPoint[] }) {
  const ref = useLeaflet(points, (map, pts) => {
    const maxStarts = Math.max(...pts.map((p) => p.starts), 1);
    for (const p of pts) {
      const meta = PLATFORM_META[p.platform_code];
      const color = meta ? cssColor(meta.varName, "#4f46e5") : "#4f46e5";
      L.circleMarker([p.latitude, p.longitude], {
        radius: 3 + Math.sqrt(p.starts / maxStarts) * 9,
        color,
        weight: 1,
        opacity: 0.9,
        fillColor: color,
        fillOpacity: 0.5,
      })
        .bindPopup(`<strong>${p.location_name}</strong><br>${pluralizeRu(p.starts, COUNT_FORMS.events)}`)
        .addTo(map);
    }
  });
  return <div ref={ref} className="portal-geo-leaflet" />;
}

/* B. Кластеры с числами (агрегация по сетке, распад при зуме) */
function VariantClusters({ points }: { points: PortalGeoPoint[] }) {
  const ref = useLeaflet(points, (map, pts) => {
    const layer = L.layerGroup().addTo(map);
    const accent = cssColor("--accent-indigo", "#4f46e5");

    const render = () => {
      layer.clearLayers();
      if (map.getZoom() >= 7) {
        for (const p of pts) {
          L.circleMarker([p.latitude, p.longitude], {
            radius: 6,
            color: accent,
            weight: 1,
            fillColor: accent,
            fillOpacity: 0.6,
          })
            .bindPopup(`<strong>${p.location_name}</strong><br>${pluralizeRu(p.starts, COUNT_FORMS.events)}`)
            .addTo(layer);
        }
        return;
      }
      const cells = new Map<string, { lat: number; lon: number; count: number }>();
      const cellPx = 64;
      for (const p of pts) {
        const cp = map.latLngToContainerPoint([p.latitude, p.longitude]);
        const key = `${Math.round(cp.x / cellPx)}:${Math.round(cp.y / cellPx)}`;
        const cell = cells.get(key);
        if (cell) {
          cell.lat += p.latitude;
          cell.lon += p.longitude;
          cell.count += 1;
        } else {
          cells.set(key, { lat: p.latitude, lon: p.longitude, count: 1 });
        }
      }
      for (const cell of cells.values()) {
        const lat = cell.lat / cell.count;
        const lon = cell.lon / cell.count;
        const size = cell.count > 30 ? 44 : cell.count > 9 ? 36 : 28;
        L.marker([lat, lon], {
          icon: L.divIcon({
            className: "portal-cluster-icon",
            html: `<span style="width:${size}px;height:${size}px;">${formatInt(cell.count)}</span>`,
            iconSize: [size, size],
          }),
        }).addTo(layer);
      }
    };
    render();
    map.on("zoomend moveend", render);
  });
  return <div ref={ref} className="portal-geo-leaflet" />;
}

/* C. Хороплет регионов */
function VariantChoropleth({ points }: { points: PortalGeoPoint[] }) {
  const ref = useLeaflet(points, (map, pts) => {
    const counts = new Map<string, { locations: number; starts: number; name: string }>();
    for (const p of pts) {
      if (!p.region) continue;
      const key = regionKey(p.region);
      const entry = counts.get(key) ?? { locations: 0, starts: 0, name: p.region };
      entry.locations += 1;
      entry.starts += p.starts;
      counts.set(key, entry);
    }
    const maxLocations = Math.max(1, ...[...counts.values()].map((c) => c.locations));
    const accent = cssColor("--accent-indigo", "#4f46e5");

    fetch("/russia-regions.geojson")
      .then((r) => r.json())
      .then((geo: GeoJSON.FeatureCollection) => {
        L.geoJSON(geo, {
          style: (feature) => {
            const name = (feature?.properties as { name?: string } | undefined)?.name ?? "";
            const entry = counts.get(regionKey(name));
            const share = entry ? Math.sqrt(entry.locations / maxLocations) : 0;
            return {
              color: "var(--border-heavy)",
              weight: 0.7,
              fillColor: accent,
              fillOpacity: entry ? 0.12 + share * 0.55 : 0.03,
            };
          },
          onEachFeature: (feature, layer) => {
            const name = (feature.properties as { name?: string }).name ?? "";
            const entry = counts.get(regionKey(name));
            layer.bindTooltip(
              entry
                ? `<strong>${name}</strong><br>${pluralizeRu(entry.locations, COUNT_FORMS.locations)} · ${pluralizeRu(entry.starts, COUNT_FORMS.events)}`
                : `<strong>${name}</strong><br>локаций пока нет`,
              { sticky: true },
            );
          },
        }).addTo(map);
      })
      .catch(() => undefined);
  });
  return <div ref={ref} className="portal-geo-leaflet" />;
}

/* D. Тепловая карта (свечение плотности) */
function VariantHeat({ points }: { points: PortalGeoPoint[] }) {
  const ref = useLeaflet(points, (map, pts) => {
    const pane = map.createPane("portal-heat");
    pane.style.filter = "blur(11px)";
    pane.style.opacity = "0.82";
    const maxStarts = Math.max(...pts.map((p) => p.starts), 1);
    for (const p of pts) {
      const weight = Math.sqrt(p.starts / maxStarts);
      L.circleMarker([p.latitude, p.longitude], {
        pane: "portal-heat",
        radius: 9 + weight * 20,
        stroke: false,
        fillColor: weight > 0.55 ? "#ef4444" : weight > 0.25 ? "#f59e0b" : "#6366f1",
        fillOpacity: 0.24 + weight * 0.45,
      }).addTo(map);
    }
    for (const p of pts) {
      L.circleMarker([p.latitude, p.longitude], {
        radius: 2,
        stroke: false,
        fillColor: "#ffffff",
        fillOpacity: 0.55,
      })
        .bindPopup(`<strong>${p.location_name}</strong><br>${pluralizeRu(p.starts, COUNT_FORMS.events)}`)
        .addTo(map);
    }
  });
  return <div ref={ref} className="portal-geo-leaflet" />;
}

const VARIANTS = [
  {
    id: "A",
    title: "Точки в цветах систем",
    note: "Каждая локация — кружок цвета своей системы, размер — число стартов. Сразу видно зоны 5 вёрст, островки S95 и архивную географию.",
    component: VariantPlatformDots,
  },
  {
    id: "B",
    title: "Кластеры с числами",
    note: "На обзорном зуме точки схлопываются в кружки с числом локаций (как в 2ГИС), при приближении рассыпаются в отдельные точки с попапами.",
    component: VariantClusters,
  },
  {
    id: "C",
    title: "Хороплет регионов",
    note: "Регионы залиты по числу локаций, тултип — локации и старты региона. Читается как «карта покрытия страны», хорошо стыкуется с хороплетом в кабинете.",
    component: VariantChoropleth,
  },
  {
    id: "D",
    title: "Тепловая карта",
    note: "Свечение плотности: жёлто-красные очаги — самые беговые агломерации. Эффектно, но конкретную локацию найти сложнее (белые точки кликабельны).",
    component: VariantHeat,
  },
] as const;

export function PortalMapLab() {
  const [points, setPoints] = useState<PortalGeoPoint[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchPortalHome()
      .then((data) => setPoints(data.geo.points))
      .catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить данные"));
  }, []);

  return (
    <>
      <PortalHeader />
      <main className="portal-home">
        <section className="portal-hero" style={{ paddingBottom: 18 }}>
          <p className="portal-eyebrow">Лаборатория</p>
          <h1>Карта географии: 4 варианта</h1>
          <p className="portal-hero-lead">
            Одни и те же {points ? points.length : "…"} локаций — четыре способа показать их на
            главной. Все карты живые: зум, попапы, обе темы.
          </p>
        </section>
        {error && <p className="portal-error">{error}</p>}
        {!points && !error && <p className="portal-loading">Загрузка…</p>}
        {points &&
          VARIANTS.map((variant) => (
            <section className="portal-panel" key={variant.id}>
              <div className="portal-panel-head">
                <div>
                  <h2>
                    Вариант {variant.id} — {variant.title}
                  </h2>
                  <p className="portal-panel-sub">{variant.note}</p>
                </div>
              </div>
              <variant.component points={points} />
            </section>
          ))}
      </main>
    </>
  );
}
