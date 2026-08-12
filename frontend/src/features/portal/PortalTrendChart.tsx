import { useMemo, useRef, useState } from "react";
import { formatInt } from "../../lib/format";

export const PLATFORM_CHART_META: Record<string, { title: string; cssVar: string }> = {
  five_verst: { title: "5 вёрст", cssVar: "var(--accent-green-text)" },
  s95: { title: "S95", cssVar: "var(--link)" },
  runpark: { title: "RunPark", cssVar: "var(--tint-orange-text)" },
  parkrun: { title: "parkrun", cssVar: "var(--tint-violet-text)" },
};

const PLATFORM_ORDER = ["five_verst", "s95", "runpark", "parkrun"];

export type TrendPoint = {
  label: string;
  platforms: Record<string, number>;
};

type HoverState = {
  index: number;
  anchorX: number;
};

function formatPct(delta: number): string {
  const rounded = Math.round(delta);
  return `${rounded > 0 ? "+" : ""}${rounded.toLocaleString("ru-RU")}%`;
}

/** Мультилинейный график по системам: линия на систему, ховер-тултип с деталями.
 *  showGrowth: в тултипе к каждой системе добавляется рост в % к предыдущей точке.
 *  projection: прогноз на конец текущего (неполного) периода при сохранении темпа.
 *  От предпоследней точки идёт развилка — сплошная линия к факту и пунктир к
 *  прогнозу, обе оканчиваются на ОДНОМ X (последняя точка). При ховере на неё
 *  тултип показывает факт и прогноз двумя колонками. */
export function PortalTrendChart({
  points,
  totalLabel,
  showGrowth = false,
  projection = null,
}: {
  points: TrendPoint[];
  totalLabel?: string;
  showGrowth?: boolean;
  projection?: TrendPoint | null;
}) {
  const width = 720;
  const height = 230;
  const plot = { left: 58, right: 708, top: 14, bottom: 196 };
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [hover, setHover] = useState<HoverState | null>(null);

  const codes = useMemo(
    () =>
      PLATFORM_ORDER.filter((code) =>
        points.some((point) => (point.platforms[code] ?? 0) > 0),
      ),
    [points],
  );

  const maxValue = useMemo(
    () =>
      Math.max(
        1,
        ...points.flatMap((point) => codes.map((code) => point.platforms[code] ?? 0)),
        ...(projection ? codes.map((code) => projection.platforms[code] ?? 0) : []),
      ),
    [points, codes, projection],
  );

  if (points.length < 2) {
    return <p className="portal-empty-note">Недостаточно данных для графика.</p>;
  }

  const lastIndex = points.length - 1;
  const stepX = (plot.right - plot.left) / lastIndex;
  const xFor = (index: number) => plot.left + index * stepX;
  const yFor = (value: number) =>
    plot.bottom - (value / maxValue) * (plot.bottom - plot.top);

  const gridValues = [0.25, 0.5, 0.75, 1].map((share) => Math.round(maxValue * share));
  const labelEvery = Math.max(1, Math.ceil(points.length / 6));

  const handleMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const svg = event.currentTarget;
    const rect = svg.getBoundingClientRect();
    const scaleX = width / rect.width;
    const px = (event.clientX - rect.left) * scaleX;
    const index = Math.min(lastIndex, Math.max(0, Math.round((px - plot.left) / stepX)));
    setHover({ index, anchorX: (xFor(index) / width) * rect.width });
  };

  const hoverPoint = hover ? points[hover.index] : null;
  const hoverTotal = hoverPoint
    ? codes.reduce((sum, code) => sum + (hoverPoint.platforms[code] ?? 0), 0)
    : 0;
  const isProjectionHover = hover !== null && hover.index === lastIndex && projection !== null;
  const projectionTotal = projection
    ? codes.reduce((sum, code) => sum + (projection.platforms[code] ?? 0), 0)
    : 0;
  const prevYearTotal =
    lastIndex > 0
      ? codes.reduce((sum, code) => sum + (points[lastIndex - 1]?.platforms[code] ?? 0), 0)
      : 0;
  const projectionTotalGrowth =
    prevYearTotal > 0 ? ((projectionTotal - prevYearTotal) / prevYearTotal) * 100 : null;
  const tooltipOnLeft = hover !== null && hover.index > points.length / 2;

  return (
    <div className="portal-chart-wrap" ref={wrapRef}>
      <svg
        className="portal-chart-svg"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="График по системам"
        onMouseMove={handleMove}
        onMouseLeave={() => setHover(null)}
      >
        {gridValues.map((value) => (
          <g key={value}>
            <line
              x1={plot.left}
              x2={plot.right}
              y1={yFor(value)}
              y2={yFor(value)}
              stroke="var(--border-soft)"
            />
            <text x={plot.left - 6} y={yFor(value) + 3} textAnchor="end">
              {value >= 10_000 ? `${Math.round(value / 1000)} тыс` : formatInt(value)}
            </text>
          </g>
        ))}
        <line
          x1={plot.left}
          x2={plot.right}
          y1={plot.bottom}
          y2={plot.bottom}
          stroke="var(--border)"
        />
        {codes.map((code) => {
          const meta = PLATFORM_CHART_META[code];
          // Линию ведём только там, где система реально существовала:
          // нулевые участки — разрыв, а не полка на нуле.
          const segments: string[][] = [];
          let current: string[] = [];
          points.forEach((point, index) => {
            const value = point.platforms[code] ?? 0;
            if (value > 0) {
              current.push(`${xFor(index).toFixed(1)},${yFor(value).toFixed(1)}`);
            } else if (current.length > 0) {
              segments.push(current);
              current = [];
            }
          });
          if (current.length > 0) {
            segments.push(current);
          }
          // Развилка прогноза: от ПРЕДПОСЛЕДНЕЙ точки пунктиром до прогноза —
          // на ТОМ ЖЕ X, что и последняя (фактическая) точка, а не отдельным слотом.
          const lastValue = points[lastIndex].platforms[code] ?? 0;
          const prevValue = lastIndex > 0 ? points[lastIndex - 1].platforms[code] ?? 0 : 0;
          const projectedValue = projection?.platforms[code] ?? 0;
          const showProjection =
            projection != null && lastIndex > 0 && prevValue > 0 && projectedValue > lastValue;
          return (
            <g key={code}>
              {segments.map((segment, segmentIndex) =>
                segment.length === 1 ? (
                  <circle
                    key={`${code}-${segmentIndex}`}
                    cx={segment[0].split(",")[0]}
                    cy={segment[0].split(",")[1]}
                    r="2.5"
                    fill={meta.cssVar}
                  />
                ) : (
                  <polyline
                    key={`${code}-${segmentIndex}`}
                    points={segment.join(" ")}
                    fill="none"
                    stroke={meta.cssVar}
                    strokeWidth="2"
                    strokeLinejoin="round"
                    strokeLinecap="round"
                  />
                ),
              )}
              {showProjection && (
                <>
                  <line
                    x1={xFor(lastIndex - 1)}
                    y1={yFor(prevValue)}
                    x2={xFor(lastIndex)}
                    y2={yFor(projectedValue)}
                    stroke={meta.cssVar}
                    strokeWidth="2"
                    strokeDasharray="4 4"
                    strokeLinecap="round"
                    opacity="0.75"
                  />
                  <circle
                    cx={xFor(lastIndex)}
                    cy={yFor(projectedValue)}
                    r="3.5"
                    fill="var(--surface)"
                    stroke={meta.cssVar}
                    strokeWidth="2"
                  />
                </>
              )}
            </g>
          );
        })}
        {hover !== null && (
          <g>
            <line
              x1={xFor(hover.index)}
              x2={xFor(hover.index)}
              y1={plot.top}
              y2={plot.bottom}
              stroke="var(--border-heavy)"
              strokeDasharray="3 3"
            />
            {codes
              .filter((code) => (points[hover.index].platforms[code] ?? 0) > 0)
              .map((code) => (
                <circle
                  key={code}
                  cx={xFor(hover.index)}
                  cy={yFor(points[hover.index].platforms[code] ?? 0)}
                  r="4"
                  fill={PLATFORM_CHART_META[code].cssVar}
                  stroke="var(--surface)"
                  strokeWidth="1.5"
                />
              ))}
          </g>
        )}
        {points.map(
          (point, index) =>
            (index % labelEvery === 0 || index === points.length - 1) && (
              <text key={point.label} x={xFor(index)} y={height - 8} textAnchor="middle">
                {point.label}
              </text>
            ),
        )}
      </svg>

      {hoverPoint && hover && !isProjectionHover && (
        <div
          className="portal-chart-tooltip"
          style={
            tooltipOnLeft
              ? { right: `calc(100% - ${hover.anchorX}px + 12px)` }
              : { left: hover.anchorX + 12 }
          }
        >
          <div className="portal-chart-tooltip-title">{hoverPoint.label}</div>
          {codes
            .filter((code) => (hoverPoint.platforms[code] ?? 0) > 0)
            .map((code) => {
              const value = hoverPoint.platforms[code] ?? 0;
              // Рост в % к предыдущей точке, где у системы уже были данные.
              let growth: number | null = null;
              if (showGrowth) {
                const prev = points[hover.index - 1]?.platforms[code] ?? 0;
                if (prev > 0) {
                  growth = ((value - prev) / prev) * 100;
                }
              }
              return (
                <div className="portal-chart-tooltip-row" key={code}>
                  <i style={{ background: PLATFORM_CHART_META[code].cssVar }} />
                  <span>{PLATFORM_CHART_META[code].title}</span>
                  {growth != null && (
                    <span
                      className={`portal-chart-growth ${growth >= 0 ? "up" : "down"}`}
                    >
                      {formatPct(growth)}
                    </span>
                  )}
                  <b className="num">{formatInt(value)}</b>
                </div>
              );
            })}
          {totalLabel && (
            <div className="portal-chart-tooltip-row portal-chart-tooltip-total">
              <span>{totalLabel}</span>
              <b className="num">{formatInt(hoverTotal)}</b>
            </div>
          )}
        </div>
      )}

      {isProjectionHover && hover && projection && (
        <div
          className="portal-chart-tooltip portal-chart-tooltip-split"
          style={
            tooltipOnLeft
              ? { right: `calc(100% - ${hover.anchorX}px + 12px)` }
              : { left: hover.anchorX + 12 }
          }
        >
          <div className="portal-chart-tooltip-title">{hoverPoint?.label}</div>
          <div className="portal-chart-tooltip-cols">
            <span />
            <span>Факт</span>
            <span>Прогноз</span>
          </div>
          {codes
            .filter(
              (code) =>
                (hoverPoint?.platforms[code] ?? 0) > 0 || (projection.platforms[code] ?? 0) > 0,
            )
            .map((code) => {
              const prevYearValue = lastIndex > 0 ? points[lastIndex - 1]?.platforms[code] ?? 0 : 0;
              const projectedValue = projection.platforms[code] ?? 0;
              const projectedGrowth =
                prevYearValue > 0 ? ((projectedValue - prevYearValue) / prevYearValue) * 100 : null;
              return (
                <div className="portal-chart-tooltip-cols" key={code}>
                  <span className="portal-chart-tooltip-name">
                    <i style={{ background: PLATFORM_CHART_META[code].cssVar }} />
                    {PLATFORM_CHART_META[code].title}
                  </span>
                  <b className="num">{formatInt(hoverPoint?.platforms[code] ?? 0)}</b>
                  <span className="portal-chart-tooltip-projected-cell">
                    <b className="num portal-chart-tooltip-projected">
                      {formatInt(projectedValue)}
                    </b>
                    {projectedGrowth != null && (
                      <i
                        className={`portal-chart-tooltip-projected-growth ${
                          projectedGrowth >= 0 ? "up" : "down"
                        }`}
                      >
                        ≈{formatPct(projectedGrowth)}
                      </i>
                    )}
                  </span>
                </div>
              );
            })}
          {totalLabel && (
            <div className="portal-chart-tooltip-cols portal-chart-tooltip-total">
              <span>{totalLabel}</span>
              <b className="num">{formatInt(hoverTotal)}</b>
              <span className="portal-chart-tooltip-projected-cell">
                <b className="num portal-chart-tooltip-projected">{formatInt(projectionTotal)}</b>
                {projectionTotalGrowth != null && (
                  <i
                    className={`portal-chart-tooltip-projected-growth ${
                      projectionTotalGrowth >= 0 ? "up" : "down"
                    }`}
                  >
                    ≈{formatPct(projectionTotalGrowth)}
                  </i>
                )}
              </span>
            </div>
          )}
        </div>
      )}

      <div className="portal-chart-legend">
        {codes.map((code) => (
          <span key={code} className="portal-chart-legend-item">
            <i style={{ background: PLATFORM_CHART_META[code].cssVar }} />
            {PLATFORM_CHART_META[code].title}
          </span>
        ))}
        {projection && (
          <span className="portal-chart-legend-item portal-chart-legend-projection">
            <i className="portal-chart-legend-dash" />
            прогноз на конец года
          </span>
        )}
      </div>
    </div>
  );
}
