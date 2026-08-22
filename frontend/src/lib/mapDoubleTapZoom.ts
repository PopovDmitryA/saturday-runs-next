import L from "leaflet";

/**
 * Жест «двойной тап и потянуть» — как в Яндекс.Картах и Google Maps: тапнул
 * дважды, второй палец не отпустил и повёл вверх/вниз — карта плавно
 * приближается и отдаляется. Одной рукой, без щипка (просьба Дмитрия
 * 22.08.2026).
 *
 * В Leaflet такого хендлера нет: из коробки он знает только щипок (`touchZoom`)
 * и двойной тап как «приблизить на уровень» (`doubleClickZoom`). Ничего
 * платформенного в жесте при этом нет — обычные touch-события, поэтому в вебе
 * на телефоне он работает так же, как в нативном приложении.
 *
 * Пока жест идёт, `zoomSnap` временно ставим в 0: иначе Leaflet округляет зум
 * до целого, и вместо плавного приближения выходят рывки через уровень. После
 * жеста снап возвращается — кнопки «+/−» снова ходят по целым уровням.
 */

/** Второй тап должен начаться не позже этого срока после первого. */
const DOUBLE_TAP_MS = 320;
/** …и не дальше этого расстояния от него — иначе это два разных тапа. */
const TAP_SLOP_PX = 32;
/**
 * Сколько уровней зума даёт пиксель вертикали. 1/110 — экран телефона (~700 px
 * рабочей высоты карты) проходит примерно шесть уровней от края до края:
 * дотянуться от области до района можно одним движением, но и попасть в
 * нужный масштаб не сложно.
 */
const ZOOM_PER_PX = 1 / 110;
/**
 * Не чаще, чем раз в кадр. requestAnimationFrame специально не используем: в
 * фоновых вкладках и в превью-панели он не тикает, и жест бы замирал.
 */
const FRAME_MS = 16;

export function addDoubleTapDragZoom(map: L.Map): () => void {
  const container = map.getContainer();

  let lastTapAt = 0;
  let lastTapPoint: { x: number; y: number } | null = null;

  let active = false;
  let startY = 0;
  let startZoom = 0;
  let anchor: L.LatLng | null = null;
  let lastMoveAt = 0;
  let snapBeforeGesture = map.options.zoomSnap;
  let dragWasEnabled = false;
  let doubleClickWasEnabled = false;

  const stop = () => {
    if (!active) {
      return;
    }
    active = false;
    anchor = null;
    map.options.zoomSnap = snapBeforeGesture;
    if (dragWasEnabled) {
      map.dragging.enable();
    }
    if (doubleClickWasEnabled) {
      map.doubleClickZoom.enable();
    }
    // Гасим «первый тап», чтобы отпускание пальца после жеста не оказалось
    // началом нового двойного тапа.
    lastTapAt = 0;
    lastTapPoint = null;
  };

  const onTouchStart = (event: TouchEvent) => {
    if (event.touches.length !== 1) {
      // Щипок двумя пальцами — не наш случай, отдаём его штатному touchZoom.
      stop();
      return;
    }
    const touch = event.touches[0];
    const point = { x: touch.clientX, y: touch.clientY };
    const now = event.timeStamp || performance.now();
    const isSecondTap =
      lastTapPoint !== null &&
      now - lastTapAt < DOUBLE_TAP_MS &&
      Math.abs(point.x - lastTapPoint.x) < TAP_SLOP_PX &&
      Math.abs(point.y - lastTapPoint.y) < TAP_SLOP_PX;

    if (!isSecondTap) {
      lastTapAt = now;
      lastTapPoint = point;
      return;
    }

    active = true;
    startY = point.y;
    startZoom = map.getZoom();
    // Масштабируем вокруг точки касания, а не вокруг центра карты: палец стоит
    // на том месте, которое человек и хочет рассмотреть.
    const bounds = container.getBoundingClientRect();
    anchor = map.containerPointToLatLng(
      L.point(point.x - bounds.left, point.y - bounds.top),
    );
    snapBeforeGesture = map.options.zoomSnap;
    map.options.zoomSnap = 0;
    dragWasEnabled = map.dragging.enabled();
    doubleClickWasEnabled = map.doubleClickZoom.enabled();
    // Перетаскивание на время жеста выключаем: иначе карта поедет за пальцем
    // вместе с зумом. Двойной тап Leaflet'а — тоже, чтобы он не прибавил свой
    // уровень поверх нашего.
    map.dragging.disable();
    map.doubleClickZoom.disable();
    lastMoveAt = 0;
    event.preventDefault();
  };

  const onTouchMove = (event: TouchEvent) => {
    if (!active || anchor === null) {
      return;
    }
    if (event.touches.length !== 1) {
      stop();
      return;
    }
    // Страница не должна прокручиваться под жестом. Слушатель неленивый
    // (passive: false), иначе preventDefault браузер проигнорирует.
    event.preventDefault();
    const now = event.timeStamp || performance.now();
    if (now - lastMoveAt < FRAME_MS) {
      return;
    }
    lastMoveAt = now;
    // Ведём вверх — приближаем: так же, как в Яндекс.Картах.
    const delta = (startY - event.touches[0].clientY) * ZOOM_PER_PX;
    const zoom = Math.max(
      map.getMinZoom(),
      Math.min(map.getMaxZoom(), startZoom + delta),
    );
    if (Math.abs(zoom - map.getZoom()) < 0.01) {
      return;
    }
    map.setZoomAround(anchor, zoom, { animate: false });
  };

  const onTouchEnd = () => {
    stop();
  };

  // Ставимся на фазу перехвата: обработчики Leaflet'а висят на том же
  // контейнере, и нам важно успеть выключить перетаскивание до них.
  const capture = { capture: true, passive: false } as const;
  container.addEventListener("touchstart", onTouchStart, capture);
  container.addEventListener("touchmove", onTouchMove, capture);
  container.addEventListener("touchend", onTouchEnd, capture);
  container.addEventListener("touchcancel", onTouchEnd, capture);

  return () => {
    stop();
    container.removeEventListener("touchstart", onTouchStart, capture);
    container.removeEventListener("touchmove", onTouchMove, capture);
    container.removeEventListener("touchend", onTouchEnd, capture);
    container.removeEventListener("touchcancel", onTouchEnd, capture);
  };
}
