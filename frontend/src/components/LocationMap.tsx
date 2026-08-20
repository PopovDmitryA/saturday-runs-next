import { useEffect, useRef, type ReactNode } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { MapLocationPoint } from "../lib/api";
import { formatDate, formatInt, platformCodeLabel, pluralFormRu } from "../lib/format";
import type { MapViewportRef } from "../lib/mapViewport";
import { addZoomControl } from "../lib/mapZoomControl";
import { MapFullscreenButton } from "./MapFullscreenButton";

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

// Точка с числом рядом (карта туристов): к обычному кружку системы приписан
// счётчик. Иконки собираются на лету и кэшируются — их до полутора тысяч, и
// пересоздавать их на каждый рендер незачем.
const countIconCache = new Map<string, L.DivIcon>();

function withCountBadge(
  base: L.DivIcon,
  count: number,
  selected: boolean,
): L.DivIcon {
  const options = base.options;
  const size = (options.iconSize as [number, number]) ?? [14, 14];
  const cacheKey = `${options.className}|${count}|${selected ? 1 : 0}`;
  const cached = countIconCache.get(cacheKey);
  if (cached) {
    return cached;
  }
  const className = [
    options.className,
    "map-marker-counted",
    count === 0 ? "map-marker-counted-empty" : "",
    selected ? "map-marker-counted-selected" : "",
  ]
    .filter(Boolean)
    .join(" ");
  // Ноль подписываем так же, как всё остальное, только бледнее: гасить саму
  // точку нельзя (решение Дмитрия 15.08.2026) — площадка на карте есть, и то,
  // что до неё никто из сотни не доехал, само по себе новость.
  const icon = L.divIcon({
    className,
    html: `<span aria-hidden="true"></span><b class="map-marker-count${
      count === 0 ? " map-marker-count-zero" : ""
    }">${count}</b>`,
    iconSize: size,
    iconAnchor: (options.iconAnchor as [number, number]) ?? [size[0] / 2, size[1] / 2],
    popupAnchor: (options.popupAnchor as [number, number]) ?? [0, -8],
  });
  countIconCache.set(cacheKey, icon);
  return icon;
}

// Сторона ячейки сетки кластеризации в пикселях. Плашка с числом туристов
// занимает ~40×18, поэтому ячейка чуть крупнее: точки, чьи подписи налезли бы
// друг на друга, схлопываются в один кружок.
const CLUSTER_CELL_PX = 52;

const clusterIconCache = new Map<string, L.DivIcon>();

/** Кружок «здесь N локаций» — им подменяется гроздь точек на мелком зуме. */
function clusterIcon(count: number, hasSelected = false): L.DivIcon {
  const cacheKey = `${count}|${hasSelected ? 1 : 0}`;
  const cached = clusterIconCache.get(cacheKey);
  if (cached) {
    return cached;
  }
  // Три ступени размера: у трёхзначных чисел иначе не хватает места. Подпись
  // «локаций» под числом — чтобы кружок нельзя было прочитать как количество
  // туристов на одной площадке.
  const size = count < 10 ? 42 : count < 100 ? 46 : 54;
  const icon = L.divIcon({
    className: hasSelected ? "map-cluster map-cluster-selected" : "map-cluster",
    html: `<span>${count}<b>${pluralFormRu(count, ["локация", "локации", "локаций"])}</b></span>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
  clusterIconCache.set(cacheKey, icon);
  return icon;
}

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
  /**
   * Карта туристов: сколько человек было на площадке (ключ — catalog_identity_key).
   * Число рисуется рядом с точкой, безымянные точки гаснут.
   */
  countsByIdentity?: Map<string, number>;
  /** Выбранные точки и обработчик клика — этим карта управляет таблицей рейтинга. */
  selectedIdentities?: string[];
  onSelectPoint?: (identityKey: string, name: string) => void;
  /** Приписка в попапе выбранной точки (например, «показано в таблице»). */
  countLabel?: (count: number) => string;
  /** Кнопка «Посмотреть людей в таблице» в попапе карты туристов. */
  onShowDetails?: (identityKey: string, name: string) => void;
};

// Стартовая область каталога: Европа + вся Россия до Японии (просьба Дмитрия
// 25.07.2026) — фиксированная, а не fit по всем маркерам: одиночные далёкие
// точки (закрытые зарубежные parkrun) раздували вьюпорт на весь мир.
const CATALOG_DEFAULT_BOUNDS = L.latLngBounds([34, -11], [72, 150]);

function fitMapToPoints(map: L.Map, points: MapLocationPoint[], variant: "visited" | "catalog") {
  // Без анимации: moveend срабатывает синхронно, и общий вьюпорт записывается
  // сразу (анимированный fit мог не завершиться в фоновой вкладке).
  if (variant === "catalog") {
    map.fitBounds(CATALOG_DEFAULT_BOUNDS, { animate: false });
    return;
  }
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
  if (pageSlug) {
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
  // Строка карты туристов («здесь были N из топ-1000») — на тачскринах попап
  // единственный способ прочитать число, всплывающей подсказки там нет.
  countLine?: string | null,
): string {
  const visitedInfo =
    variant === "catalog" && point.catalog_identity_key
      ? visitedByIdentity?.get(point.catalog_identity_key)
      : undefined;
  const stats = visitedInfo ?? point;
  const locationUrl = pickLocationUrl(point, stats);
  const pageSlug = point.location_slug ?? stats.location_slug ?? null;

  // Ссылки на сайт системы в попапе нет (решение Дмитрия 15.08.2026): из
  // заголовка человек попадает на нашу страницу локации, а официальная ссылка
  // есть уже там. Внешний адрес остаётся запасным вариантом для заголовка —
  // у точки без своей страницы иначе не было бы ссылки вовсе.
  const lines = [formatPopupTitle(point.name, locationUrl, pageSlug)];
  if (countLine) {
    lines.push(`<div class="map-popup-line map-popup-tourists">${escapeHtml(countLine)}</div>`);
    // Кнопка вниз к таблице: клик по точке уже зажёг светофоры, но таблица
    // может быть за пределами экрана — из попапа до неё один шаг.
    lines.push(
      '<div class="map-popup-line"><button type="button" class="map-popup-link map-popup-details">Посмотреть людей в таблице ↓</button></div>',
    );
  }
  if (point.city) {
    lines.push(`<div class="map-popup-line">${escapeHtml(point.city)}</div>`);
  }
  if (point.region && point.region !== point.city) {
    lines.push(`<div class="map-popup-line">${escapeHtml(point.region)}</div>`);
  }

  // «Не действует» сильнее отмены: у неработающей площадки сообщать про
  // отменённую субботу нечего.
  if (point.is_paused || stats.is_paused) {
    lines.push(`<div class="map-popup-line map-popup-paused">Не действует</div>`);
  } else if (point.is_cancelled || stats.is_cancelled) {
    lines.push(`<div class="map-popup-line map-popup-cancelled">Отмена ближайшего старта</div>`);
  } else if (point.is_upcoming || stats.is_upcoming) {
    lines.push(`<div class="map-popup-line map-popup-upcoming">Скоро откроется</div>`);
  }

  if (variant === "visited" || visitedInfo) {
    lines.push(
      `<div class="map-popup-line">Пробежек: ${formatInt(stats.run_count)}, волонтёрств: ${formatInt(stats.volunteer_count)}</div>`,
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
  countsByIdentity,
  selectedIdentities,
  onSelectPoint,
  countLabel,
  onShowDetails,
}: LocationMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markersRef = useRef<L.LayerGroup | null>(null);
  const hasFittedInitialViewRef = useRef(false);
  // Актуальное значение active для обработчика moveend (он регистрируется один раз).
  const activeRef = useRef(active);
  activeRef.current = active;
  // Перерисовка слоя маркеров: её дёргает и смена зума (состав кластеров), и
  // смена выбранной площадки.
  const drawRef = useRef<(() => void) | null>(null);
  // Нарисованные одиночные точки и кластеры: по ним подсветка выбора идёт
  // точечно, без пересборки слоя.
  const singleMarkersRef = useRef(new Map<string, { marker: L.Marker; icon: L.DivIcon }>());
  const clusterMarkersRef = useRef<{ marker: L.Marker; count: number; keys: string[] }[]>([]);
  const selectedRef = useRef<Set<string>>(new Set(selectedIdentities));
  const onSelectPointRef = useRef(onSelectPoint);
  onSelectPointRef.current = onSelectPoint;
  const onShowDetailsRef = useRef(onShowDetails);
  onShowDetailsRef.current = onShowDetails;

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
      zoomControl: false,
    }).setView([55.75, 37.62], 5);

    addZoomControl(map);
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

    const buildMarker = (point: MapLocationPoint): L.Marker => {
      const visitedInfo =
        variant === "catalog" && point.catalog_identity_key
          ? visitedByIdentity?.get(point.catalog_identity_key)
          : undefined;
      const isCancelled = Boolean(point.is_cancelled || visitedInfo?.is_cancelled);
      const isPaused = Boolean(point.is_paused || visitedInfo?.is_paused);
      const isUpcoming = Boolean(point.is_upcoming || visitedInfo?.is_upcoming);
      const identity = point.catalog_identity_key ?? null;
      const count = countsByIdentity && identity ? countsByIdentity.get(identity) ?? 0 : null;
      const baseIcon = markerIconForPoint(point, variant, visitedByIdentity);
      const icon =
        count === null
          ? baseIcon
          : withCountBadge(
              baseIcon as L.DivIcon,
              count,
              identity != null && selectedRef.current.has(identity),
            );
      const marker = L.marker([point.latitude, point.longitude], { icon });
      marker.bindPopup(
        popupHtml(point, variant, visitedByIdentity, count === null ? null : countLabel?.(count)),
      );
      if (count !== null && identity) {
        // Клик по точке — выбор площадки: именно он зажигает светофоры в
        // таблице рейтинга. Попап при этом остаётся: в нём подробности точки.
        marker.on("click", () => onSelectPointRef.current?.(identity, point.name));
        // Кнопка живёт в HTML попапа, поэтому обработчик вешаем при открытии:
        // до этого её узла в документе нет.
        marker.on("popupopen", (event) => {
          const button = event.popup
            .getElement()
            ?.querySelector<HTMLButtonElement>(".map-popup-details");
          button?.addEventListener("click", () =>
            onShowDetailsRef.current?.(identity, point.name),
          );
        });
        marker.bindTooltip(count > 0 ? `${point.name}: ${count}` : point.name, {
          direction: "top",
          opacity: 0.92,
          className: "map-marker-tooltip",
        });
      } else if (isPaused) {
        marker.bindTooltip("Не действует", {
          direction: "top",
          opacity: 0.92,
          className: "map-marker-tooltip",
        });
      } else if (isCancelled) {
        marker.bindTooltip("Отмена ближайшего старта", {
          direction: "top",
          opacity: 0.92,
          className: "map-marker-tooltip",
        });
      } else if (isUpcoming) {
        marker.bindTooltip("Скоро откроется", {
          direction: "top",
          opacity: 0.92,
          className: "map-marker-tooltip",
        });
      }
      return marker;
    };

    // Карта туристов: на мелком зуме подписи с числами налезали друг на друга
    // сплошной кашей (скриншот Дмитрия 15.08.2026 — Москва). Гроздь точек
    // схлопывается в кружок с числом ПЛОЩАДОК, и число туристов показывается
    // только у той точки, что стоит на карте одна. Складывать туристов в
    // кластере нельзя: на соседних площадках это во многом одни и те же люди.
    const drawClustered = () => {
      const zoom = map.getZoom();
      const cells = new Map<string, MapLocationPoint[]>();
      for (const point of points) {
        // project() даёт координаты в пикселях всего мира на этом зуме, а не
        // относительно экрана, — поэтому сетка не «дышит» при перетаскивании.
        const projected = map.project([point.latitude, point.longitude], zoom);
        const key = `${Math.floor(projected.x / CLUSTER_CELL_PX)}:${Math.floor(
          projected.y / CLUSTER_CELL_PX,
        )}`;
        const bucket = cells.get(key);
        if (bucket) {
          bucket.push(point);
        } else {
          cells.set(key, [point]);
        }
      }

      for (const group of cells.values()) {
        if (group.length === 1) {
          const point = group[0];
          const marker = buildMarker(point);
          if (point.catalog_identity_key) {
            singleMarkersRef.current.set(point.catalog_identity_key, {
              marker,
              icon: markerIconForPoint(point, variant, visitedByIdentity) as L.DivIcon,
            });
          }
          marker.addTo(markers);
          continue;
        }
        const bounds = L.latLngBounds(
          group.map((point) => [point.latitude, point.longitude] as [number, number]),
        );
        const keys = group
          .map((point) => point.catalog_identity_key)
          .filter((key): key is string => Boolean(key));
        const cluster = L.marker(bounds.getCenter(), {
          icon: clusterIcon(group.length, keys.some((key) => selectedRef.current.has(key))),
        });
        cluster.bindTooltip(
          `${group.length} ${pluralFormRu(group.length, ["локация", "локации", "локаций"])} рядом — нажмите, чтобы приблизить`,
          { direction: "top", opacity: 0.92, className: "map-marker-tooltip" },
        );
        cluster.on("click", () => {
          // Гроздь из точек, стоящих буквально в одном парке, границами не
          // разъедешь — тогда просто подкручиваем зум.
          if (bounds.getNorthEast().equals(bounds.getSouthWest())) {
            map.setView(bounds.getCenter(), Math.min(zoom + 3, map.getMaxZoom()));
          } else {
            // fitBounds, а не flyToBounds: плавный «полёт» живёт на
            // requestAnimationFrame, и в фоновой вкладке он не доигрывает —
            // карта застревала между зумами, а кластеры не пересобирались.
            map.fitBounds(bounds.pad(0.3), {
              maxZoom: Math.min(zoom + 4, map.getMaxZoom()),
            });
          }
        });
        clusterMarkersRef.current.push({ marker: cluster, count: group.length, keys });
        cluster.addTo(markers);
      }
    };

    const draw = () => {
      markers.clearLayers();
      singleMarkersRef.current.clear();
      clusterMarkersRef.current = [];
      if (points.length === 0) {
        return;
      }
      if (countsByIdentity) {
        drawClustered();
        return;
      }
      for (const point of points) {
        buildMarker(point).addTo(markers);
      }
    };

    drawRef.current = draw;
    draw();

    if (!hasFittedInitialViewRef.current) {
      const shared = viewportRef?.current;
      if (shared) {
        map.setView([shared.lat, shared.lng], shared.zoom, { animate: false });
      } else {
        fitMapToPoints(map, points, variant);
      }
      hasFittedInitialViewRef.current = true;
    }

    // Состав кластеров зависит только от зума (сетка живёт в мировых пикселях),
    // поэтому перерисовываем на zoomend, а не на каждое движение карты.
    if (!countsByIdentity) {
      return;
    }
    map.on("zoomend", draw);
    return () => {
      map.off("zoomend", draw);
    };
  }, [points, variant, visitedByIdentity, viewportRef, countsByIdentity, countLabel]);

  // Смена набора выбранных площадок: подменяем иконки только у затронутых
  // маркеров. Пересобирать слой целиком нельзя — clearLayers() уничтожает
  // маркер вместе с открытым попапом, и подсказка захлопывалась в тот же миг,
  // когда человек кликал по точке (репорт Дмитрия 15.08.2026).
  // В зависимости идёт строковый ключ набора, а не сам массив: он приходит
  // новой ссылкой на каждый рендер и гонял бы обновление вхолостую.
  const selectedKey = (selectedIdentities ?? []).join("|");
  useEffect(() => {
    const previous = selectedRef.current;
    const next = new Set(selectedKey ? selectedKey.split("|") : []);
    selectedRef.current = next;
    if (!countsByIdentity) {
      return;
    }
    const changed = new Set<string>();
    for (const key of previous) {
      if (!next.has(key)) changed.add(key);
    }
    for (const key of next) {
      if (!previous.has(key)) changed.add(key);
    }
    for (const key of changed) {
      const entry = singleMarkersRef.current.get(key);
      if (entry) {
        entry.marker.setIcon(
          withCountBadge(entry.icon, countsByIdentity.get(key) ?? 0, next.has(key)),
        );
      }
    }
    // Кластер, внутри которого что-то выбрали, тоже помечаем: иначе выбор
    // площадки в московской грозди никак не виден на карте.
    for (const cluster of clusterMarkersRef.current) {
      if (!cluster.keys.some((key) => changed.has(key))) {
        continue;
      }
      cluster.marker.setIcon(
        clusterIcon(cluster.count, cluster.keys.some((key) => next.has(key))),
      );
    }
  }, [selectedKey, countsByIdentity]);

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
