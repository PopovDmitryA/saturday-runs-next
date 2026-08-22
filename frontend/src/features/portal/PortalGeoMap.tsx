import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { trackHomeLinkClick } from "../../lib/abTest";
import { addZoomControl } from "../../lib/mapZoomControl";
import { PLATFORM_CHART_META } from "./PortalTrendChart";
import type { PortalGeoPoint } from "./portalTypes";

/** Россия с ближним зарубежьем: Калининград — Чукотка, Кавказ — Заполярье. */
const HOME_REGION = { minLat: 40, maxLat: 78, minLon: 19, maxLon: 191 };

function inHomeRegion(point: PortalGeoPoint): boolean {
  return (
    point.latitude >= HOME_REGION.minLat &&
    point.latitude <= HOME_REGION.maxLat &&
    point.longitude >= HOME_REGION.minLon &&
    point.longitude <= HOME_REGION.maxLon
  );
}

/** Попап собирается строкой — имя локации из БД экранируем. */
function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Заголовок попапа: название локации ссылкой на её страницу, если слаг известен. */
function popupTitle(point: PortalGeoPoint): string {
  const name = escapeHtml(point.location_name);
  if (!point.location_slug) {
    return `<strong>${name}</strong>`;
  }
  const slug = encodeURIComponent(point.location_slug);
  return (
    `<strong><a class="map-popup-link" data-location-slug="${escapeHtml(point.location_slug)}"` +
    ` href="/locations/${slug}">${name}</a></strong>`
  );
}

function cssVarColor(expr: string, fallback: string): string {
  const name = expr.replace(/^var\(/, "").replace(/\)$/, "");
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

export function PortalGeoMap({ points }: { points: PortalGeoPoint[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current || points.length === 0) {
      return;
    }

    const container = containerRef.current;
    // Попапы Leaflet — это HTML-строка, обычного onClick у ссылки нет: клик
    // ловим делегированием на контейнере карты.
    const onContainerClick = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      const link = target?.closest<HTMLAnchorElement>("a[data-location-slug]");
      const slug = link?.dataset.locationSlug;
      if (slug) {
        trackHomeLinkClick("location", slug);
      }
    };
    container.addEventListener("click", onContainerClick);

    const map = L.map(containerRef.current, {
      scrollWheelZoom: false,
      attributionControl: false,
      zoomControl: false,
    });
    addZoomControl(map);
    L.control.attribution({ prefix: false }).addTo(map);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);

    const colorByPlatform: Record<string, string> = {};
    for (const [code, meta] of Object.entries(PLATFORM_CHART_META)) {
      colorByPlatform[code] = cssVarColor(meta.cssVar, "#4f46e5");
    }
    const maxStarts = Math.max(...points.map((point) => point.starts), 1);
    // Начальный вид считаем только по точкам России и ближнего зарубежья:
    // пара локаций за океаном (parkrun в Буэнос-Айресе, Анталья) иначе
    // растягивает fitBounds на оба полушария, и вся страна превращается в
    // пятно. Сами маркеры остаются на карте — до них можно доехать руками.
    const bounds = L.latLngBounds([]);
    for (const point of points) {
      const latLng = L.latLng(point.latitude, point.longitude);
      if (inHomeRegion(point)) {
        bounds.extend(latLng);
      }
      const radius = 3 + Math.sqrt(point.starts / maxStarts) * 9;
      const color = colorByPlatform[point.platform_code] ?? "#4f46e5";
      L.circleMarker(latLng, {
        radius,
        color,
        weight: 1,
        opacity: 0.9,
        fillColor: color,
        fillOpacity: 0.5,
      })
        .bindPopup(
          `${popupTitle(point)}<br>${point.starts.toLocaleString("ru-RU")} стартов`,
        )
        .addTo(map);
    }
    // Ни одной «домашней» точки (например, отфильтровали данные) — показываем
    // всё, что есть, лишь бы карта не осталась без вида вовсе.
    const viewBounds = bounds.isValid()
      ? bounds
      : L.latLngBounds(points.map((point) => L.latLng(point.latitude, point.longitude)));
    map.fitBounds(viewBounds, { padding: [24, 24] });
    mapRef.current = map;

    return () => {
      container.removeEventListener("click", onContainerClick);
      map.remove();
      mapRef.current = null;
    };
  }, [points]);

  if (points.length === 0) {
    return <p className="portal-empty-note">Координаты локаций пока не заполнены.</p>;
  }

  const legendCodes = Object.keys(PLATFORM_CHART_META).filter((code) =>
    points.some((point) => point.platform_code === code),
  );

  return (
    <>
      <div ref={containerRef} className="portal-geo-leaflet" />
      <div className="portal-chart-legend">
        {legendCodes.map((code) => (
          <span key={code} className="portal-chart-legend-item">
            <i style={{ background: PLATFORM_CHART_META[code].cssVar }} />
            {PLATFORM_CHART_META[code].title}
          </span>
        ))}
      </div>
    </>
  );
}
