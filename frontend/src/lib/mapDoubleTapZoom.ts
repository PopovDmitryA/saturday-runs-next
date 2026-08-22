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
 *
 * Двигаем карту тем же способом, что и штатный щипок Leaflet: `_move` с флагом
 * `pinch`. Флаг доходит до слоя тайлов, и тот НЕ пересобирает сетку — только
 * пересчитывает CSS-трансформ уже загруженных тайлов. Картинка плавно тянется,
 * новые тайлы приезжают один раз, когда палец отпущен.
 *
 * Раньше на каждом кадре звался `setZoomAround`, а он гонит полный `_resetView`:
 * тайлы выбрасывались и запрашивались заново по нескольку раз за жест, и в
 * прорехах мигала подложка карты (репорт Дмитрия 22.08.2026).
 */

/**
 * Внутренности Leaflet, которых нет в его тайпингах. Это ровно те же вызовы, из
 * которых собран его собственный обработчик щипка (`Map.TouchZoom`), — публичного
 * способа «подвигать карту без перезагрузки тайлов» в API нет.
 */
type LeafletMapInternals = {
  _moveStart(zoomChanged: boolean, noMoveStart: boolean): unknown;
  _move(
    center: L.LatLng,
    zoom: number,
    data?: { pinch?: boolean; round?: boolean },
    suppressEvent?: boolean,
  ): unknown;
  _limitZoom(zoom: number): number;
  _animateZoom(center: L.LatLng, zoom: number, startAnim: boolean, noUpdate?: unknown): void;
  _resetView(center: L.LatLng, zoom: number): void;
};

function internals(map: L.Map): LeafletMapInternals {
  return map as unknown as L.Map & LeafletMapInternals;
}

/**
 * Центр, при котором точка `anchor` останется под пальцем на новом зуме, — та
 * же арифметика, что внутри `Map.setZoomAround`.
 */
function centerKeepingAnchor(map: L.Map, anchor: L.LatLng, zoom: number): L.LatLng {
  const scale = map.getZoomScale(zoom);
  const viewHalf = map.getSize().divideBy(2);
  const anchorPoint = map.latLngToContainerPoint(anchor);
  const offset = anchorPoint.subtract(viewHalf).multiplyBy(1 - 1 / scale);
  return map.containerPointToLatLng(viewHalf.add(offset));
}

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
  // Карту начали двигать (был хотя бы один кадр жеста) — значит, в конце её
  // нужно «посадить»: подгрузить тайлы под итоговый масштаб.
  let moved = false;
  let lastCenter: L.LatLng | null = null;
  let lastZoom = 0;
  let snapBeforeGesture = map.options.zoomSnap;
  let dragWasEnabled = false;
  let doubleClickWasEnabled = false;

  const stop = () => {
    if (!active) {
      return;
    }
    active = false;
    anchor = null;
    // Садимся на итоговый масштаб ДО возврата снапа: с zoomSnap = 0 Leaflet
    // оставит дробный зум, на котором жест и закончился, и не дёрнет карту на
    // целый уровень. Дальше тайлы догружаются один раз — это и есть та самая
    // единственная перерисовка за весь жест.
    if (moved && lastCenter !== null) {
      const settleZoom = internals(map)._limitZoom(lastZoom);
      if (map.options.zoomAnimation) {
        internals(map)._animateZoom(lastCenter, settleZoom, true, map.options.zoomSnap);
      } else {
        internals(map)._resetView(lastCenter, settleZoom);
      }
    }
    moved = false;
    lastCenter = null;
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
    moved = false;
    lastCenter = null;
    lastZoom = startZoom;
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
    const center = centerKeepingAnchor(map, anchor, zoom);
    if (!moved) {
      internals(map)._moveStart(true, false);
      moved = true;
    }
    lastCenter = center;
    lastZoom = zoom;
    internals(map)._move(center, zoom, { pinch: true, round: false });
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
