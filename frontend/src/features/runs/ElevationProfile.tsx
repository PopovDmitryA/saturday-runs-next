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
  const [hover, setHover] = useState<{ distance: number; height: number } | null>(null);

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

  const handleMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - rect.left) / rect.width;
    const distance = Math.max(0, Math.min(maxDistance, ratio * WIDTH >= PADDING.left
      ? ((ratio * WIDTH - PADDING.left) / (WIDTH - PADDING.left - PADDING.right)) * maxDistance
      : 0));
    const index = Math.round((distance / maxDistance) * (profile.length - 1));
    const point = profile[Math.max(0, Math.min(profile.length - 1, index))];
    setHover({ distance: point[0], height: point[1] });
  };

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
      <p className="run-track-elevation-note">
        {hover
          ? `${(hover.distance / 1000).toFixed(2).replace(".", ",")} км — ${hover.height.toFixed(1).replace(".", ",")} м`
          : hasClimb
            ? `Главный подъём — ${climbLength} м ${
                // «на 0,0 км» читается как ошибка: у подъёма от самого старта
                // пишем словами.
                climbFrom < 100
                  ? "сразу от старта"
                  : `на ${(climbFrom / 1000).toFixed(1).replace(".", ",")} км`
              }, уклон ${String(metrics.climb_grade_percent).replace(".", ",")}%.`
            : "Ровная трасса: заметных подъёмов нет."}
      </p>
    </figure>
  );
}
