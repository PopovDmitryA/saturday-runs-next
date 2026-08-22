import L from "leaflet";
import { sendMapGeoPing } from "./api";

/**
 * «Где я» на карте: точка текущего положения и приближение к ней.
 *
 * Правило приближения (решение Дмитрия 22.08.2026): приближать настолько,
 * насколько можно, но так, чтобы в кадр влезла хотя бы ОДНА площадка. Иначе
 * человек в Костроме получал бы пустой двор вместо карты субботних стартов.
 * В Москве ближайшая точка рядом — и приближение выходит крупным само;
 * в глуши кадр разъезжается до ближайшего города.
 *
 * Браузер отдаёт координаты только на HTTPS (на localhost тоже) и только
 * с разрешения человека — окно спрашивает он сам, отдельной кнопки «согласен»
 * нам рисовать не нужно.
 */

/** Ближе не приближаем, даже если старт в соседнем дворе: карта квартала не нужна. */
export const MY_LOCATION_MAX_ZOOM = 14;

/** Сколько ждём ответ GPS, прежде чем сдаться. */
const GEOLOCATION_TIMEOUT_MS = 10_000;

/**
 * Ключ в localStorage: спрашивали ли мы уже. Нужен, чтобы не показывать окно
 * браузера на каждом заходе тому, кто один раз отказался.
 */
const ASKED_KEY = "map_geolocation_asked";

export type MyPosition = {
  latitude: number;
  longitude: number;
  /** Радиус погрешности в метрах — им рисуется круг вокруг точки. */
  accuracy: number;
};

/**
 * Ключ в localStorage с датой последней отправленной отметки. Нужен, чтобы не
 * слать запрос на каждое открытие карты: сервер всё равно хранит одну отметку
 * в сутки, а лишние запросы — это лишние запросы.
 */
const PING_SENT_KEY = "map_geo_ping_sent_on";

/** Два знака после запятой — клетка примерно километр на километр. */
const COORDINATE_PRECISION = 2;

function today(): string {
  const now = new Date();
  return `${now.getFullYear()}-${now.getMonth() + 1}-${now.getDate()}`;
}

/**
 * Отправить огрублённую отметку положения — не чаще раза в сутки.
 *
 * Округляем ДО отправки: точные координаты из браузера наружу не уходят вовсе.
 * Запрос фоновый, ответ никого не интересует: не дошёл — и не надо, карта
 * работает сама по себе.
 */
export function reportMyPosition(position: MyPosition): void {
  let sentOn: string | null = null;
  try {
    sentOn = window.localStorage.getItem(PING_SENT_KEY);
  } catch {
    // Приватный режим может запрещать localStorage — тогда отметка уйдёт ещё
    // раз, и её отбросит уникальный индекс на сервере.
  }
  const day = today();
  if (sentOn === day) {
    return;
  }
  try {
    window.localStorage.setItem(PING_SENT_KEY, day);
  } catch {
    // См. выше.
  }
  void sendMapGeoPing({
    latitude: Number(position.latitude.toFixed(COORDINATE_PRECISION)),
    longitude: Number(position.longitude.toFixed(COORDINATE_PRECISION)),
    accuracy_m: Number.isFinite(position.accuracy) ? Math.round(position.accuracy) : null,
  }).catch(() => {
    // Молча: отметка — не то, ради чего человек нажимал кнопку.
  });
}

export function geolocationSupported(): boolean {
  return typeof navigator !== "undefined" && "geolocation" in navigator;
}

export function hasAskedForGeolocation(): boolean {
  try {
    return window.localStorage.getItem(ASKED_KEY) === "1";
  } catch {
    // Приватный режим может запрещать localStorage — тогда просто спросим ещё раз.
    return false;
  }
}

export function rememberGeolocationAsked(): void {
  try {
    window.localStorage.setItem(ASKED_KEY, "1");
  } catch {
    // Не смогли запомнить — не беда, окно браузера покажется снова.
  }
}

/**
 * Разрешение, выданное браузером, без показа окна: `granted` — можно определять
 * молча, `denied` — не дёргать вовсе, `prompt` — окно появится. `null` — браузер
 * не умеет Permissions API (Safari до 16), решение принимаем по нашему флагу.
 */
export async function geolocationPermission(): Promise<PermissionState | null> {
  if (typeof navigator === "undefined" || !navigator.permissions?.query) {
    return null;
  }
  try {
    const status = await navigator.permissions.query({ name: "geolocation" as PermissionName });
    return status.state;
  } catch {
    return null;
  }
}

export function requestMyPosition(): Promise<MyPosition> {
  return new Promise((resolve, reject) => {
    if (!geolocationSupported()) {
      reject(new Error("Браузер не умеет определять положение"));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (position) =>
        resolve({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracy: position.coords.accuracy,
        }),
      (error) => reject(error),
      {
        enableHighAccuracy: false,
        timeout: GEOLOCATION_TIMEOUT_MS,
        // Свежесть в пять минут: карту открывают не на бегу, а положение за это
        // время не меняется настолько, чтобы это было видно на таком масштабе.
        maximumAge: 5 * 60 * 1000,
      },
    );
  });
}

/** Квадрат расстояния в градусах — для поиска ближайшей точки сравнивать хватает. */
function roughDistanceSq(
  position: MyPosition,
  point: { latitude: number; longitude: number },
): number {
  // Долготу сжимаем по широте: на 55° градус долготы вдвое короче градуса широты,
  // и без поправки «ближайшей» оказывалась площадка западнее, а не севернее.
  const scale = Math.cos((position.latitude * Math.PI) / 180);
  const dLat = point.latitude - position.latitude;
  const dLng = (point.longitude - position.longitude) * scale;
  return dLat * dLat + dLng * dLng;
}

export function nearestPoint<T extends { latitude: number; longitude: number }>(
  position: MyPosition,
  points: T[],
): T | null {
  let best: T | null = null;
  let bestDistance = Number.POSITIVE_INFINITY;
  for (const point of points) {
    const distance = roughDistanceSq(position, point);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = point;
    }
  }
  return best;
}

/**
 * Показать положение на карте и подобрать к нему масштаб.
 *
 * Кадр строим по двум точкам — человек и ближайшая к нему площадка, — и
 * ограничиваем сверху `MY_LOCATION_MAX_ZOOM`. Получается ровно то, что просили:
 * ближе некуда, но хотя бы один субботний старт в кадре есть. Площадок нет
 * вовсе (карта пустая) — просто центрируемся на человеке.
 */
/** Сколько длится перелёт к своей точке. */
const FLY_DURATION_SEC = 1.3;

/**
 * Долететь до кадра, а не прыгнуть в него.
 *
 * Полёт Leaflet живёт на requestAnimationFrame, и во вкладке, уведённой в фон,
 * он не доигрывает — карта застревает на полпути (той же граблей избегает
 * кластеризация в LocationMap). Поэтому следом ставим страховку: если к концу
 * отведённого времени кадр не тот, досаживаем его без анимации. Свой moveend
 * полёт отдаёт по завершении и страховку снимает — заодно её снимет и человек,
 * если потянет карту рукой посреди перелёта.
 */
function flyToFrame(map: L.Map, center: L.LatLng, zoom: number): void {
  const guard = window.setTimeout(
    () => {
      if (
        Math.abs(map.getZoom() - zoom) > 0.01 ||
        map.getCenter().distanceTo(center) > 50
      ) {
        map.setView(center, zoom, { animate: false });
      }
    },
    (FLY_DURATION_SEC + 0.5) * 1000,
  );
  map.once("moveend", () => window.clearTimeout(guard));
  map.flyTo(center, zoom, { duration: FLY_DURATION_SEC });
}

export function focusOnMyPosition(
  map: L.Map,
  position: MyPosition,
  points: Array<{ latitude: number; longitude: number }>,
): void {
  const me = L.latLng(position.latitude, position.longitude);
  const cap = Math.min(MY_LOCATION_MAX_ZOOM, map.getMaxZoom());
  const nearest = nearestPoint(position, points);
  // Размер контейнера пересчитываем перед подбором кадра: Leaflet держит его в
  // кэше, и на карте, которая только что была в скрытой вкладке, он устаревший.
  // На нулевой ширине fitBounds честно отвечает «нулевой зум» — и вместо своего
  // района человек получал карту всего мира.
  map.invalidateSize();
  const size = map.getSize();
  if (!nearest || size.x < 40 || size.y < 40) {
    flyToFrame(map, me, cap);
    return;
  }
  // Кадр считаем сами, а не отдаём flyToBounds: страховке нужна та же цель,
  // в которую метил полёт, — иначе она досаживала бы карту не туда.
  const bounds = L.latLngBounds([me, L.latLng(nearest.latitude, nearest.longitude)]).pad(0.35);
  flyToFrame(map, bounds.getCenter(), Math.min(cap, map.getBoundsZoom(bounds)));
}

const myPositionIcon = L.divIcon({
  className: "map-my-position",
  html: '<span aria-hidden="true"></span>',
  iconSize: [16, 16],
  iconAnchor: [8, 8],
});

/**
 * Слой «вы здесь»: точка и круг погрешности. Возвращает функцию удаления —
 * повторное определение положения сначала снимает старый слой.
 */
export function drawMyPosition(map: L.Map, position: MyPosition): L.LayerGroup {
  const me = L.latLng(position.latitude, position.longitude);
  const layer = L.layerGroup();
  // Круг рисуем только у грубого определения (по вышкам и Wi-Fi): при точности
  // в десяток метров он схлопывается в пятно под самой точкой и только мешает.
  if (position.accuracy > 100) {
    L.circle(me, {
      radius: position.accuracy,
      className: "map-my-position-accuracy",
      interactive: false,
    }).addTo(layer);
  }
  L.marker(me, { icon: myPositionIcon, interactive: true, keyboard: false })
    .bindTooltip("Вы здесь", { direction: "top", opacity: 0.92, className: "map-marker-tooltip" })
    .addTo(layer);
  layer.addTo(map);
  return layer;
}
