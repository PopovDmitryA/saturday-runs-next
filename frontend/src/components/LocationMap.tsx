import { useEffect, useRef, type ReactNode } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { MapLocationPoint } from "../lib/api";
import { formatDate, platformCodeLabel } from "../lib/format";
import type { MapViewportRef } from "../lib/mapViewport";
import { MapFullscreenButton } from "./MapFullscreenButton";
import { getCurrentUserIsAdmin } from "../lib/currentUserAdmin";

const visitedIcon = L.divIcon({
  className: "map-marker map-marker-visited",
  html: '<span aria-hidden="true"></span>',
  iconSize: [18, 18],
  iconAnchor: [9, 9],
  popupAnchor: [0, -10],
});

const catalogIcon = L.divIcon({
  className: "map-marker map-marker-catalog",
  html: '<span aria-hidden="true"></span>',
  iconSize: [14, 14],
  iconAnchor: [7, 7],
  popupAnchor: [0, -8],
});

const pausedIcon = L.divIcon({
  className: "map-marker map-marker-paused",
  html: '<span aria-hidden="true"></span>',
  iconSize: [14, 14],
  iconAnchor: [7, 7],
  popupAnchor: [0, -8],
});

const pausedVisitedIcon = L.divIcon({
  className: "map-marker map-marker-paused-visited",
  html: '<span aria-hidden="true"></span>',
  iconSize: [18, 18],
  iconAnchor: [9, 9],
  popupAnchor: [0, -10],
});

const cancelledIcon = L.divIcon({
  className: "map-marker map-marker-cancelled",
  html: '<span aria-hidden="true"></span>',
  iconSize: [14, 14],
  iconAnchor: [7, 7],
  popupAnchor: [0, -8],
});

const cancelledVisitedIcon = L.divIcon({
  className: "map-marker map-marker-cancelled-visited",
  html: '<span aria-hidden="true"></span>',
  iconSize: [18, 18],
  iconAnchor: [9, 9],
  popupAnchor: [0, -10],
});

const platformMarkerIcons: Record<string, L.DivIcon> = {
  five_verst: L.divIcon({
    className: "map-marker map-marker-five-verst",
    html: '<span aria-hidden="true"></span>',
    iconSize: [14, 14],
    iconAnchor: [7, 7],
    popupAnchor: [0, -8],
  }),
  s95: L.divIcon({
    className: "map-marker map-marker-s95",
    html: '<span aria-hidden="true"></span>',
    iconSize: [14, 14],
    iconAnchor: [7, 7],
    popupAnchor: [0, -8],
  }),
  runpark: L.divIcon({
    className: "map-marker map-marker-runpark",
    html: '<span aria-hidden="true"></span>',
    iconSize: [14, 14],
    iconAnchor: [7, 7],
    popupAnchor: [0, -8],
  }),
};

function markerIconForPoint(
  point: MapLocationPoint,
  variant: "visited" | "catalog",
  visitedByIdentity?: Map<string, MapLocationPoint>,
): L.Icon | L.DivIcon {
  const isCancelled = Boolean(point.is_cancelled);
  if (variant === "visited") {
    if (isCancelled) {
      return cancelledVisitedIcon;
    }
    return point.is_paused ? pausedVisitedIcon : visitedIcon;
  }
  if (
    point.catalog_identity_key &&
    visitedByIdentity?.has(point.catalog_identity_key)
  ) {
    const visitedInfo = visitedByIdentity.get(point.catalog_identity_key);
    if (isCancelled || visitedInfo?.is_cancelled) {
      return cancelledVisitedIcon;
    }
    if (point.is_paused || visitedInfo?.is_paused) {
      return pausedVisitedIcon;
    }
    return visitedIcon;
  }
  if (isCancelled) {
    return cancelledIcon;
  }
  if (point.is_paused) {
    return pausedIcon;
  }
  const platformCode = point.active_platform ?? point.platform_codes[0];
  if (platformCode && platformMarkerIcons[platformCode]) {
    return platformMarkerIcons[platformCode];
  }
  return catalogIcon;
}

type LocationMapProps = {
  points: MapLocationPoint[];
  variant: "visited" | "catalog";
  emptyMessage: string;
  visitedByIdentity?: Map<string, MapLocationPoint>;
  legend?: ReactNode;
  isFullscreen?: boolean;
  onToggleFullscreen?: () => void;
  /** Панель видна: при активации карта пересчитывает размер и применяет общий вьюпорт. */
  active?: boolean;
  /** Общий с картой регионов вьюпорт — чтобы положение сохранялось при переключении. */
  viewportRef?: MapViewportRef;
};

function fitMapToPoints(map: L.Map, points: MapLocationPoint[]) {
  // Без анимации: moveend срабатывает синхронно, и общий вьюпорт записывается
  // сразу (анимированный fit мог не завершиться в фоновой вкладке).
  if (points.length === 1) {
    map.setView([points[0].latitude, points[0].longitude], 12, { animate: false });
    return;
  }
  const bounds = L.latLngBounds([]);
  for (const point of points) {
    bounds.extend([point.latitude, point.longitude]);
  }
  map.fitBounds(bounds.pad(0.12), { animate: false });
}

function formatPopupDates(dates: string[] | undefined, limit = 8): string {
  if (!dates || dates.length === 0) {
    return "";
  }
  const formatted = dates.slice(0, limit).map((value) => formatDate(value));
  if (dates.length > limit) {
    return `${formatted.join(", ")} (+${dates.length - limit})`;
  }
  return formatted.join(", ");
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function pickLocationUrl(
  point: MapLocationPoint,
  stats?: MapLocationPoint,
): string | null {
  if (point.location_url) {
    return point.location_url;
  }
  if (stats?.location_url) {
    return stats.location_url;
  }
  for (const visit of stats?.platform_visits ?? point.platform_visits ?? []) {
    if (visit.location_url) {
      return visit.location_url;
    }
  }
  return null;
}

function formatPopupTitle(name: string, url: string | null, pageSlug?: string | null): string {
  const escapedName = escapeHtml(name);
  // Раздел «Локации» пока не анонсируем — остальным ведём на внешнюю систему.
  if (pageSlug && getCurrentUserIsAdmin()) {
    // Внутренняя страница локации приоритетнее внешней ссылки на систему.
    return `<strong><a class="map-popup-link" href="/locations/${encodeURIComponent(pageSlug)}">${escapedName}</a></strong>`;
  }
  if (!url) {
    return `<strong>${escapedName}</strong>`;
  }
  const escapedUrl = escapeHtml(url);
  return `<strong><a class="map-popup-link" href="${escapedUrl}" target="_blank" rel="noopener noreferrer">${escapedName}</a></strong>`;
}

function popupHtml(
  point: MapLocationPoint,
  variant: "visited" | "catalog",
  visitedByIdentity?: Map<string, MapLocationPoint>,
): string {
  const visitedInfo =
    variant === "catalog" && point.catalog_identity_key
      ? visitedByIdentity?.get(point.catalog_identity_key)
      : undefined;
  const stats = visitedInfo ?? point;
  const locationUrl = pickLocationUrl(point, stats);
  const pageSlug = point.location_slug ?? stats.location_slug ?? null;

  const lines = [formatPopupTitle(point.name, locationUrl, pageSlug)];
  if (pageSlug && locationUrl) {
    lines.push(
      `<div class="map-popup-line"><a class="map-popup-link" href="${escapeHtml(locationUrl)}" target="_blank" rel="noopener noreferrer">Официальная страница ↗</a></div>`,
    );
  }
  if (point.city) {
    lines.push(`<div class="map-popup-line">${escapeHtml(point.city)}</div>`);
  }
  if (point.region && point.region !== point.city) {
    lines.push(`<div class="map-popup-line">${escapeHtml(point.region)}</div>`);
  }

  if (point.is_cancelled || stats.is_cancelled) {
    lines.push(`<div class="map-popup-line map-popup-cancelled">Отменена</div>`);
  } else if (point.is_paused || stats.is_paused) {
    lines.push(`<div class="map-popup-line map-popup-paused">На паузе</div>`);
  }

  if (variant === "visited" || visitedInfo) {
    lines.push(
      `<div class="map-popup-line">Пробежек: ${stats.run_count}, волонтёрств: ${stats.volunteer_count}</div>`,
    );
    if (stats.last_visit_date) {
      lines.push(
        `<div class="map-popup-line">Последний визит: ${formatDate(String(stats.last_visit_date))}</div>`,
      );
    }

    const platformVisits = stats.platform_visits ?? [];
    if (platformVisits.length > 0) {
      for (const platformVisit of platformVisits) {
        const runDates = formatPopupDates(platformVisit.run_dates);
        const volunteerDates = formatPopupDates(platformVisit.volunteer_dates);
        if (runDates) {
          lines.push(
            `<div class="map-popup-line"><span class="map-popup-platform">${platformCodeLabel(platformVisit.platform_code)}:</span> ${runDates}</div>`,
          );
        }
        if (volunteerDates) {
          lines.push(
            `<div class="map-popup-line muted">Волонтёрство (${platformCodeLabel(platformVisit.platform_code)}): ${volunteerDates}</div>`,
          );
        }
      }
    } else {
      const runDates = formatPopupDates(stats.run_dates);
      const volunteerDates = formatPopupDates(stats.volunteer_dates);
      if (runDates) {
        lines.push(`<div class="map-popup-line">Пробежки: ${runDates}</div>`);
      }
      if (volunteerDates) {
        lines.push(`<div class="map-popup-line muted">Волонтёрство: ${volunteerDates}</div>`);
      }
    }
  } else {
    const platforms =
      point.platform_codes.length > 0
        ? point.platform_codes
        : point.active_platform
          ? [point.active_platform]
          : [];
    if (platforms.length > 0) {
      lines.push(
        `<div class="map-popup-line">Система: ${platforms.map(platformCodeLabel).join(", ")}</div>`,
      );
    }
  }
  return `<div class="map-popup">${lines.join("")}</div>`;
}

export function LocationMap({
  points,
  variant,
  emptyMessage,
  visitedByIdentity,
  legend,
  isFullscreen = false,
  onToggleFullscreen,
  active = true,
  viewportRef,
}: LocationMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markersRef = useRef<L.LayerGroup | null>(null);
  const hasFittedInitialViewRef = useRef(false);
  // Актуальное значение active для обработчика moveend (он регистрируется один раз).
  const activeRef = useRef(active);
  activeRef.current = active;

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    setTimeout(() => map.invalidateSize(), 50);
  }, [isFullscreen]);

  // При активации панели (переключение вкладки) карта была скрыта — пересчитываем
  // размер и подхватываем общий вьюпорт, чтобы положение сохранилось.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !active) return;
    setTimeout(() => {
      // Читаем общий вьюпорт ДО invalidateSize: тот может дёрнуть moveend уже
      // активной карты и перезаписать shared её собственным (устаревшим) видом.
      const shared = viewportRef?.current;
      map.invalidateSize();
      if (shared) {
        map.setView([shared.lat, shared.lng], shared.zoom, { animate: false });
      }
    }, 30);
  }, [active, viewportRef]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) {
      return;
    }

    const map = L.map(containerRef.current, {
      scrollWheelZoom: true,
      attributionControl: false,
    }).setView([55.75, 37.62], 5);

    L.control.attribution({ prefix: false }).addTo(map);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);

    markersRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;

    // Видимая карта — источник правды общего вьюпорта: пишем его при каждом
    // moveend (включая программный первичный fit), чтобы вторая карта открывалась
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
      markersRef.current = null;
    };
  }, [viewportRef]);

  useEffect(() => {
    const map = mapRef.current;
    const markers = markersRef.current;
    if (!map || !markers) {
      return;
    }

    markers.clearLayers();

    if (points.length === 0) {
      return;
    }

    for (const point of points) {
      const visitedInfo =
        variant === "catalog" && point.catalog_identity_key
          ? visitedByIdentity?.get(point.catalog_identity_key)
          : undefined;
      const isCancelled = Boolean(point.is_cancelled || visitedInfo?.is_cancelled);
      const isPaused = Boolean(point.is_paused || visitedInfo?.is_paused);
      const marker = L.marker([point.latitude, point.longitude], {
        icon: markerIconForPoint(point, variant, visitedByIdentity),
      });
      marker.bindPopup(popupHtml(point, variant, visitedByIdentity));
      if (isCancelled) {
        marker.bindTooltip("Отменена", {
          direction: "top",
          opacity: 0.92,
          className: "map-marker-tooltip",
        });
      } else if (isPaused) {
        marker.bindTooltip("На паузе", {
          direction: "top",
          opacity: 0.92,
          className: "map-marker-tooltip",
        });
      }
      marker.addTo(markers);
    }

    if (!hasFittedInitialViewRef.current) {
      const shared = viewportRef?.current;
      if (shared) {
        map.setView([shared.lat, shared.lng], shared.zoom, { animate: false });
      } else {
        fitMapToPoints(map, points);
      }
      hasFittedInitialViewRef.current = true;
    }
  }, [points, variant, visitedByIdentity, viewportRef]);

  return (
    <div className="location-map-shell">
      {points.length === 0 ? <p className="location-map-empty">{emptyMessage}</p> : null}
      <div ref={containerRef} className="location-map-canvas" aria-label="Карта локаций" />
      {legend && points.length > 0 ? (
        <div className="location-map-legend" aria-label="Легенда карты">
          {legend}
        </div>
      ) : null}
      {onToggleFullscreen && (
        <MapFullscreenButton isFullscreen={isFullscreen} onToggle={onToggleFullscreen} />
      )}
    </div>
  );
}
