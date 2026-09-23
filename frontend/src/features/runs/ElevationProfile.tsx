// Профиль трассы: по горизонтали километраж, по вертикали высота.
// Главный подъём подсвечен — на нём и теряют время.

import { useMemo, useState } from "react";
import type { RunTrackMetrics } from "../../lib/api";

const WIDTH = 640;
const HEIGHT = 190;
const PADDING = { top: 14, right: 10, bottom: 26, left: 40 };
// Минимальный размах шкалы высоты: на плоской трассе без него график
// превращается в кардиограмму из шума в полметра.
const MIN_SPAN_M = 8;

type ElevationProfileProps = {
  metrics: RunTrackMetrics;
  /** Набор высоты по прибору: он считает по сырым секундным данным и всегда
   *  больше, чем набор по сглаженному профилю. В подписи показываем именно
   *  его, чтобы цифра совпадала с плиткой «набор высоты». */
  deviceGainM?: number | null;
};

export function ElevationProfile({ metrics, deviceGainM }: ElevationProfileProps) {
  const profile = metrics.elevation_profile ?? [];
  const [hover, setHover] = useState<{ distance: number; height: number; index: number } | null>(null);

  const geometry = useMemo(() => {
    if (profile.length < 2) {
      return null;
    }
    const distances = profile.map(([distance]) => distance);
    const heights = profile.map(([, height]) => height);
    const maxDistance = distances[distances.length - 1] || 1;
    const minHeight = Math.min(...heights);
    const maxHeight = Math.max(...heights);
    // Симметричный запас вокруг реального диапазона, но не уже MIN_SPAN_M.
    const span = Math.max(maxHeight - minHeight, MIN_SPAN_M);
    const middle = (maxHeight + minHeight) / 2;
    const low = middle - span / 2;
    const high = middle + span / 2;

    const plotWidth = WIDTH - PADDING.left - PADDING.right;
    const plotHeight = HEIGHT - PADDING.top - PADDING.bottom;
    const x = (distance: number) => PADDING.left + (distance / maxDistance) * plotWidth;
    const y = (height: number) => PADDING.top + (1 - (height - low) / (high - low)) * plotHeight;

    const line = profile.map(([distance, height], index) =>
      `${index === 0 ? "M" : "L"}${x(distance).toFixed(1)} ${y(height).toFixed(1)}`,
    ).join("");
    const baseline = HEIGHT - PADDING.bottom;
    const area = `${line}L${x(maxDistance).toFixed(1)} ${baseline}L${x(0).toFixed(1)} ${baseline}Z`;

    const kilometreTicks: number[] = [];
    for (let km = 1; km * 1000 < maxDistance; km += 1) {
      kilometreTicks.push(km);
    }

    return { x, y, line, area, baseline, maxDistance, minHeight, maxHeight, low, high, kilometreTicks };
  }, [profile]);

  if (!geometry) {
    return null;
  }

  const { x, y, line, area, baseline, maxDistance, minHeight, maxHeight, kilometreTicks } = geometry;
  const climbFrom = metrics.climb_from_m;
  const climbLength = metrics.climb_length_m;
  const hasClimb = climbFrom != null && climbLength != null && climbLength > 0;

  // Уклон в точке считаем по соседям: одна пара точек профиля — это 25 метров
  // на пятёрке, на таком плече уклон уже осмысленный, а шум ещё не правит бал.
  const gradeAt = (index: number): number | null => {
    const from = profile[Math.max(0, index - 1)];
    const to = profile[Math.min(profile.length - 1, index + 1)];
    const run = to[0] - from[0];
    return run > 0 ? ((to[1] - from[1]) / run) * 100 : null;
  };

  const pick = (clientX: number, target: SVGSVGElement) => {
    const rect = target.getBoundingClientRect();
    const plotWidth = WIDTH - PADDING.left - PADDING.right;
    const insideSvg = ((clientX - rect.left) / rect.width) * WIDTH;
    const distance = Math.max(
      0,
      Math.min(maxDistance, ((insideSvg - PADDING.left) / plotWidth) * maxDistance),
    );
    const index = Math.max(
      0,
      Math.min(profile.length - 1, Math.round((distance / maxDistance) * (profile.length - 1))),
    );
    setHover({ distance: profile[index][0], height: profile[index][1], index });
  };

  const handleMove = (event: React.MouseEvent<SVGSVGElement>) => pick(event.clientX, event.currentTarget);
  // Палец работает так же, как мышь: на телефоне график иначе просто мёртвый.
  const handleTouch = (event: React.TouchEvent<SVGSVGElement>) => {
    const touch = event.touches[0];
    if (touch) {
      pick(touch.clientX, event.currentTarget);
    }
  };

  const hoverGrade = hover ? gradeAt(hover.index) : null;
  // За серединой графика подсказку вешаем слева от курсора, иначе у правого
  // края она упиралась в стенку окна.
  const hoverOnRight = hover != null && x(hover.distance) > WIDTH / 2;
  const inClimb =
    hasClimb && hover != null && hover.distance >= climbFrom && hover.distance <= climbFrom + climbLength;

  return (
    <figure className="run-track-elevation">
      <figcaption>
        Профиль трассы · перепад {Math.round(maxHeight - minHeight)} м
        {(deviceGainM ?? metrics.elevation_gain_profile_m) != null &&
          `, набор ${Math.round(deviceGainM ?? metrics.elevation_gain_profile_m ?? 0)} м`}
      </figcaption>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label="График высоты по дистанции"
        onMouseMove={handleMove}
        onMouseLeave={() => setHover(null)}
        onTouchStart={handleTouch}
        onTouchMove={handleTouch}
        onTouchEnd={() => setHover(null)}
      >
        {hasClimb && (
          // Подсветка главного подъёма — фоном под графиком, чтобы не спорить с линией.
          <rect
            x={x(climbFrom)}
            y={PADDING.top}
            width={Math.max(2, x(climbFrom + climbLength) - x(climbFrom))}
            height={baseline - PADDING.top}
            className="climb-band"
          />
        )}
        {kilometreTicks.map((km) => (
          <g key={km}>
            <line x1={x(km * 1000)} y1={PADDING.top} x2={x(km * 1000)} y2={baseline} className="grid" />
            <text x={x(km * 1000)} y={HEIGHT - 8} textAnchor="middle" className="axis">
              {km} км
            </text>
          </g>
        ))}
        <path d={area} className="area" />
        <path d={line} className="line" />
        <text x={PADDING.left - 6} y={y(maxHeight) + 4} textAnchor="end" className="axis">
          {Math.round(maxHeight)}
        </text>
        <text x={PADDING.left - 6} y={y(minHeight) + 4} textAnchor="end" className="axis">
          {Math.round(minHeight)}
        </text>
        {hover && (
          <g>
            <line x1={x(hover.distance)} y1={PADDING.top} x2={x(hover.distance)} y2={baseline} className="cursor" />
            <circle cx={x(hover.distance)} cy={y(hover.height)} r={3.5} className="dot" />
          </g>
        )}
      </svg>
      {/* Подсказку рисуем не в SVG, а слоем поверх: ширину текста в SVG не
          измерить, и коробочка фиксированной ширины вылезала за край окна.
          Здесь ширину считает браузер, а край ловится обычным left/right. */}
      {hover && (
        <div
          className="run-track-hover"
          style={
            hoverOnRight
              ? { right: `${(1 - x(hover.distance) / WIDTH) * 100}%` }
              : { left: `${(x(hover.distance) / WIDTH) * 100}%` }
          }
        >
          <b>
            {(hover.distance / 1000).toFixed(2).replace(".", ",")} км ·{" "}
            {hover.height.toFixed(1).replace(".", ",")} м
          </b>
          <span>
            {hoverGrade == null
              ? "уклон —"
              : Math.abs(hoverGrade) < 0.3
                ? "ровно"
                : `уклон ${hoverGrade > 0 ? "+" : "−"}${Math.abs(hoverGrade)
                    .toFixed(1)
                    .replace(".", ",")}%`}
            {" · "}
            {hover.height === maxHeight
              ? "высшая точка"
              : hover.height === minHeight
                ? "низшая точка"
                : `+${(hover.height - minHeight).toFixed(1).replace(".", ",")} м от низа`}
            {inClimb && " · главный подъём"}
          </span>
        </div>
      )}
      <p className="run-track-elevation-note">
        {hasClimb ? (
          <>
            {/* Подсветку надо назвать: иначе непонятно, что за цветной
                прямоугольник на части графика. */}
            <span className="climb-legend" aria-hidden />
            {`Закрашен главный подъём — ${climbLength} м ${
              // «на 0,0 км» читается как ошибка: у подъёма от самого старта
              // пишем словами.
              climbFrom < 100
                ? "сразу от старта"
                : `на ${(climbFrom / 1000).toFixed(1).replace(".", ",")} км`
            }, набор ${String(metrics.climb_rise_m).replace(".", ",")} м, уклон ${String(
              metrics.climb_grade_percent,
            ).replace(".", ",")}%.`}
          </>
        ) : (
          "Ровная трасса: заметных подъёмов нет."
        )}
      </p>
      {metrics.elevation_drift_m != null && Math.abs(metrics.elevation_drift_m) >= 1 && (
        // Честно говорим, что высоты правлены: иначе цифры расходятся с тем,
        // что человек видит в приложении часов.
        <p className="run-track-elevation-note muted">
          Барометр за пробежку уполз на{" "}
          {Math.abs(metrics.elevation_drift_m).toFixed(1).replace(".", ",")} м — это видно по тому, что
          финиш «выше» старта, хотя это одна и та же точка. Дрейф снят, профиль показан без него.
        </p>
      )}
    </figure>
  );
}
