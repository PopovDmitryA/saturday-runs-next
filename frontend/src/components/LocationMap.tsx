import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { getMapPointContext, type MapLocationPoint, type MapPointContext } from "../lib/api";
import { formatDate, formatInt, formatKm, platformCodeLabel, pluralFormRu } from "../lib/format";
import { addDoubleTapDragZoom } from "../lib/mapDoubleTapZoom";
import {
  drawMyPosition,
  focusOnMyPosition,
  geolocationPermission,
  geolocationSupported,
  hasAskedForGeolocation,
  rememberGeolocationAsked,
  reportMyPosition,
  requestMyPosition,
} from "../lib/mapGeolocation";
import type { MapViewportRef } from "../lib/mapViewport";
import { addZoomControl } from "../lib/mapZoomControl";
import { MapFullscreenButton } from "./MapFullscreenButton";
import { MapMyLocationButton } from "./MapMyLocationButton";

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
  /**
   * Показывать кнопку «где я» и спрашивать геопозицию при открытии карты.
   * На чужом профиле не нужна — там смотрят чужие визиты, а не свою округу.
   */
  myLocation?: boolean;
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
    // Открываем в новой вкладке (просьба Дмитрия 22.08.2026): карта — рабочий
    // стол планирования, человек ходит по нескольким точкам подряд, и уводить
    // его со сложенного вьюпорта ради одной страницы незачем.
    return `<strong><a class="map-popup-link" href="/locations/${encodeURIComponent(pageSlug)}" target="_blank" rel="noopener noreferrer">${escapedName}</a></strong>`;
  }
  if (!url) {
    return `<strong>${escapedName}</strong>`;
  }
  const escapedUrl = escapeHtml(url);
  return `<strong><a class="map-popup-link" href="${escapedUrl}" target="_blank" rel="noopener noreferrer">${escapedName}</a></strong>`;
}

/** «2026-08-29» → «29.08»: год в прогнозе на ближайшие недели только мешает. */
function shortDate(iso: string): string {
  const [, month, day] = iso.split("-");
  return day && month ? `${day}.${month}` : iso;
}

/**
 * Даст ли этот старт +1 в «Нумераторе» — одной строкой на оба зачёта
 * (формулировка Дмитрия 22.08.2026): «+1 в «Нумераторе»: 5 вёрст — да;
 * общий зачёт — нет». До этого на то же самое уходило три строки попапа.
 *
 * Зачёта два, и это не дублирование: клетку челленджа закрывает пробежка в
 * ЛЮБОЙ системе, но тот же челлендж считается и в разрезе одной системы
 * (фильтр на странице достижений). Старт №137 на С95 у того, кто №137 уже брал
 * на 5 вёрстах, в сквозном зачёте не даст ничего, а в зачёте С95 — даст.
 * Механику объясняет подсказка по title — сайт показывает такие и по тапу.
 */
function plusOneLine(
  title: string,
  platformCode: string,
  gainedOverall: boolean,
  gainedPlatform: boolean,
): string {
  const answer = (gained: boolean) =>
    `<b class="${gained ? "map-popup-plus-yes" : "map-popup-plus-no"}">${
      gained ? "да" : "нет"
    }</b>`;
  const hint =
    "Клетку «Нумератора» закрывает старт с этим номером в любой системе — это общий зачёт. " +
    "Тот же челлендж считается и внутри одной системы, поэтому ответа два.";
  return (
    `<div class="map-popup-line map-popup-plus-line" title="${escapeHtml(hint)}">` +
    `+1 в «${escapeHtml(title)}»: ${escapeHtml(platformCodeLabel(platformCode))} — ${answer(
      gainedPlatform,
    )}; общий зачёт — ${answer(gainedOverall)}</div>`
  );
}

function nextStartHtml(entry: MapPointContext["next_starts"][number]): string {
  const lines: string[] = [];
  // «≈» — не кокетство: точного расписания у нас нет, номер и дата откручены от
  // последнего известного старта, и локация вправе пропустить субботу.
  lines.push(
    `<div class="map-popup-line map-popup-next">Ближайший старт: <strong>№${entry.number}</strong> ≈ ${escapeHtml(
      shortDate(entry.date),
    )} · ${escapeHtml(platformCodeLabel(entry.platform_code))}</div>`,
  );
  // Приписки «площадка пропускала субботы» тут нет намеренно: дату мы называем,
  // а перенос старта на неделю с тем же номером и так очевиден (решение Дмитрия
  // 22.08.2026). Про номер вне диапазонов «Нумератора» тоже не пишем — просто не
  // выводим строку челленджа: номер старта человек видит и сам.
  if (entry.plus_one_overall === null || entry.plus_one_platform === null) {
    return lines.join("");
  }
  lines.push(
    plusOneLine(
      entry.challenge_title ?? "Нумератор",
      entry.platform_code,
      entry.plus_one_overall,
      entry.plus_one_platform,
    ),
  );
  return lines.join("");
}

function homeDistanceHtml(home: NonNullable<MapPointContext["home_distance"]>): string {
  const changeLink =
    '<a class="map-popup-link map-popup-home-change" href="/settings#home-location" target="_blank" rel="noopener noreferrer">сменить дом</a>';
  if (home.is_home) {
    return `<div class="map-popup-line map-popup-home">Это ваша домашняя локация · ${changeLink}</div>`;
  }
  const distance = home.distance_km == null ? "—" : formatKm(home.distance_km);
  // Приписку «выбран автоматически» показываем только у авто-дома: у выбранного
  // руками сомневаться не в чем, а ссылка всё равно рядом.
  const auto = home.home_is_auto ? " (выбран автоматически)" : "";
  return (
    `<div class="map-popup-line map-popup-home">От дома «${escapeHtml(home.home_name)}»${escapeHtml(auto)}: ` +
    `<strong>${escapeHtml(distance)}</strong> · ${changeLink}</div>`
  );
}

function pointContextHtml(context: MapPointContext): string {
  const lines = context.next_starts.map(nextStartHtml);
  if (context.home_distance) {
    lines.push(homeDistanceHtml(context.home_distance));
  }
  return lines.join("");
}

/**
 * Системы площадки плашками рядом с названием. Отдельной строкой «Система: …»
 * они не нужны (решение Дмитрия 22.08.2026): цвет плашки тот же, что у точки на
 * карте и у кнопки фильтра, — строка только съедала высоту попапа.
 */
function platformChips(point: MapLocationPoint, stats: MapLocationPoint): string {
  const codes =
    point.platform_codes.length > 0
      ? point.platform_codes
      : point.active_platform
        ? [point.active_platform]
        : (stats.platform_visits ?? point.platform_visits ?? []).map(
            (visit) => visit.platform_code,
          );
  const unique = [...new Set(codes)].filter(Boolean);
  if (unique.length === 0) {
    return "";
  }
  return unique
    .map(
      (code) =>
        `<span class="map-popup-system map-popup-system-${escapeHtml(code)}">${escapeHtml(
          platformCodeLabel(code),
        )}</span>`,
    )
    .join("");
}

function popupHtml(
  point: MapLocationPoint,
  variant: "visited" | "catalog",
  visitedByIdentity?: Map<string, MapLocationPoint>,
  // Строка карты туристов («здесь были N из топ-1000») — на тачскринах попап
  // единственный способ прочитать число, всплывающей подсказки там нет.
  countLine?: string | null,
  // Готовый HTML блока личного контекста. По умолчанию — заглушка «считаю»:
  // он приезжает отдельным запросом уже после открытия попапа.
  contextBody?: string,
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
  const lines = [
    `<div class="map-popup-title">${formatPopupTitle(
      point.name,
      locationUrl,
      pageSlug,
    )}${platformChips(point, stats)}</div>`,
  ];
  if (countLine) {
    lines.push(`<div class="map-popup-line map-popup-tourists">${escapeHtml(countLine)}</div>`);
    // Кнопка вниз к таблице: клик по точке уже зажёг светофоры, но таблица
    // может быть за пределами экрана — из попапа до неё один шаг.
    lines.push(
      '<div class="map-popup-line"><button type="button" class="map-popup-link map-popup-details">Посмотреть людей в таблице ↓</button></div>',
    );
  }
  // Город и регион — одной строкой («Екатеринбург — Свердловская»): порознь они
  // занимали две строки попапа, а читаются всё равно как один адрес. Регион,
  // повторяющий город (Москва, Питер), не дублируем.
  // Регион не приписываем, если он уже сидит в названии города: у части
  // зарубежных площадок город записан как «Аргентина, Буэнос-Айрес», и строка
  // выходила «Аргентина, Буэнос-Айрес — Буэнос-Айрес».
  const city = point.city ?? "";
  const region = point.region && !city.includes(point.region) ? point.region : null;
  const place = [city, region].filter(Boolean).join(" — ");
  if (place) {
    lines.push(`<div class="map-popup-line">${escapeHtml(place)}</div>`);
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

  // Место под личный контекст точки: номер ближайшего старта, «+1 в Нумераторе»
  // и дальность от дома. Оно приезжает отдельным запросом по клику — считать
  // это для всех трёх тысяч точек каталога разом незачем (см. popupopen ниже).
  if (point.catalog_identity_key) {
    lines.push(
      `<div class="map-popup-context">${
        contextBody ??
        '<span class="muted map-popup-context-loading">Считаю ближайший старт…</span>'
      }</div>`,
    );
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
  }
  // Строки «Система: …» тут больше нет — системы висят плашками у названия.
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
  myLocation = false,
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
  // Личный контекст точек, уже загруженный в этой сессии карты: человек кликает
  // по одним и тем же площадкам туда-сюда, и второй запрос за тем же ответом
  // не нужен. Живёт в ref, а не в модуле, — данные личные, и при смене
  // пользователя карта пересоздаётся вместе с кэшем.
  const pointContextCacheRef = useRef(new Map<string, MapPointContext>());
  // Карта уже наведена на положение человека («где я»). Общий вьюпорт поверх
  // него не применяем: иначе кадр отбрасывало бы обратно на всю страну.
  const myFocusAppliedRef = useRef(false);

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
      if (shared && !myFocusAppliedRef.current) {
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
    const removeDoubleTapZoom = addDoubleTapDragZoom(map);
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
      removeDoubleTapZoom();
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

    /**
     * Догрузить личный контекст точки и перерисовать попап вместе с ним.
     *
     * Именно перерисовать через `setPopupContent`, а не подменить innerHTML у
     * слота: Leaflet держит исходную строку и восстанавливает её из неё при
     * каждом `update()` — вписанные в DOM строки тут же затирались обратно
     * заглушкой «считаю».
     */
    const fillPointContext = async (
      marker: L.Marker,
      identity: string,
      render: (contextBody?: string) => string,
      // Перерисовка пересоздаёт узлы попапа, и обработчики на кнопках внутри
      // него теряются — их вешают заново этим коллбэком.
      onRendered: () => void,
    ) => {
      const apply = (body: string) => {
        // Попап мог закрыться (или открыться другой), пока летел запрос —
        // тогда переписывать содержимое незачем.
        if (!marker.isPopupOpen()) {
          return;
        }
        marker.setPopupContent(render(body));
        onRendered();
      };
      const cached = pointContextCacheRef.current.get(identity);
      if (cached) {
        apply(pointContextHtml(cached));
        return;
      }
      try {
        const context = await getMapPointContext(identity);
        pointContextCacheRef.current.set(identity, context);
        apply(pointContextHtml(context));
      } catch {
        // Молча: попап и без прогноза остаётся полезным, а сообщение об ошибке
        // сети в облачке над точкой человеку ничего не даёт.
        apply("");
      }
    };

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
      const countLine = count === null ? null : countLabel?.(count) ?? null;
      const render = (contextBody?: string) =>
        popupHtml(point, variant, visitedByIdentity, countLine, contextBody);
      marker.bindPopup(render());
      // Кнопка «Посмотреть людей в таблице» живёт в HTML попапа, поэтому
      // обработчик вешаем после каждой отрисовки: и при открытии, и когда
      // догрузившийся контекст перерисовал содержимое.
      const bindDetailsButton = () => {
        if (count === null || !identity) {
          return;
        }
        const button = marker
          .getPopup()
          ?.getElement()
          ?.querySelector<HTMLButtonElement>(".map-popup-details");
        button?.addEventListener("click", () =>
          onShowDetailsRef.current?.(identity, point.name),
        );
      };
      if (identity) {
        marker.on("popupopen", () => {
          void fillPointContext(marker, identity, render, bindDetailsButton);
        });
      }
      if (count !== null && identity) {
        // Клик по точке — выбор площадки: именно он зажигает светофоры в
        // таблице рейтинга. Попап при этом остаётся: в нём подробности точки.
        marker.on("click", () => onSelectPointRef.current?.(identity, point.name));
        marker.on("popupopen", bindDetailsButton);
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

  // ---------------------------------------------------------------------------
  // «Где я»: точка текущего положения и приближение к ближайшим стартам.

  const [locateState, setLocateState] = useState<"idle" | "loading" | "ready" | "denied">(
    "idle",
  );
  const myPositionLayerRef = useRef<L.LayerGroup | null>(null);
  // Точки нужны обработчику кнопки, а он создаётся один раз — держим их в ref.
  const pointsRef = useRef(points);
  pointsRef.current = points;
  const autoLocateDoneRef = useRef(false);

  const locate = useCallback(async () => {
    const map = mapRef.current;
    if (!map || !geolocationSupported()) {
      return;
    }
    setLocateState("loading");
    rememberGeolocationAsked();
    try {
      const position = await requestMyPosition();
      const current = mapRef.current;
      if (!current) {
        return;
      }
      myPositionLayerRef.current?.remove();
      myPositionLayerRef.current = drawMyPosition(current, position);
      reportMyPosition(position);
      focusOnMyPosition(current, position, pointsRef.current);
      myFocusAppliedRef.current = true;
      setLocateState("ready");
    } catch {
      // Отказ, таймаут GPS и «нет геолокации» для кнопки одно и то же: показать
      // нечего, и подсказка в титле объясняет, где включить.
      setLocateState("denied");
    }
  }, []);

  useEffect(() => {
    if (!myLocation || !active || autoLocateDoneRef.current || points.length === 0) {
      return;
    }
    if (!geolocationSupported()) {
      return;
    }
    // Отметку ставим до первого await: в dev React вызывает эффект дважды, и
    // без неё окно браузера показывалось бы два раза подряд.
    autoLocateDoneRef.current = true;
    void (async () => {
      const permission = await geolocationPermission();
      if (permission === "denied") {
        setLocateState("denied");
        return;
      }
      // Разрешение уже выдано — определяемся молча. Не выдано, но окно человек
      // однажды уже видел, — второй раз сами не показываем: остаётся кнопка.
      if (permission !== "granted" && hasAskedForGeolocation()) {
        return;
      }
      void locate();
    })();
    // Флага «отменено» тут нет намеренно: снимать размонтированную карту не от
    // чего (locate() сам перечитывает mapRef), а лишний setState в React 18 —
    // пустая операция. С флагом же двойной вызов эффекта в dev гасил бы первый
    // — единственный — заход, и автоопределение в разработке не работало вовсе.
  }, [myLocation, active, points.length, locate]);

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
      {myLocation && geolocationSupported() && points.length > 0 && (
        <MapMyLocationButton state={locateState} onClick={() => void locate()} />
      )}
    </div>
  );
}
