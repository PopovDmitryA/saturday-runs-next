// Общие виджеты страниц статистики админки: плитка с числом и столбики по дням.
import type { CSSProperties } from "react";
import { ChartColumnTooltip } from "../../components/ChartColumnTooltip";
import { formatChartDay, formatDate, formatInt, formatStatValue } from "../../lib/format";

export const CHART_BAR_AREA_PX = 112;

export type DayPoint = { date: string; value: number };

export function maxValue(items: DayPoint[]): number {
  return items.reduce((max, item) => Math.max(max, item.value), 0);
}

function sumValues(items: DayPoint[]): number {
  return items.reduce((sum, item) => sum + item.value, 0);
}

export function chartGridStyle(count: number): CSSProperties {
  return {
    gridTemplateColumns: `repeat(${Math.max(count, 1)}, minmax(0, 1fr))`,
  };
}

export function DailyBarChart({
  title,
  data,
  ariaLabel,
  barClassName = "analytics-chart-bar-run",
  valueLabel,
  chartKey,
}: {
  title: string;
  data: DayPoint[];
  ariaLabel: string;
  barClassName?: string;
  valueLabel?: (value: number) => string;
  chartKey: string;
}) {
  const peak = maxValue(data);
  const periodTotal = sumValues(data);
  const formatValue = valueLabel ?? ((value: number) => formatInt(value));
  const showBarValues = data.length <= 14;
  // Подпись дня под каждым столбиком сливается уже на месяце — оставляем каждую N-ю.
  const labelStep = Math.max(1, Math.ceil(data.length / 12));

  if (peak === 0) {
    return (
      <section className="card admin-stats-chart-card">
        <h3 className="admin-stats-chart-title">{title}</h3>
        <p className="muted">За выбранный период данных пока нет.</p>
      </section>
    );
  }

  return (
    <section className="card admin-stats-chart-card" key={chartKey}>
      <h3 className="admin-stats-chart-title">{title}</h3>
      <p className="admin-stats-chart-summary muted">За период: {formatValue(periodTotal)}</p>
      <div className="analytics-chart">
        <div
          className="analytics-chart-bars admin-stats-daily-bars"
          style={chartGridStyle(data.length)}
          role="img"
          aria-label={ariaLabel}
        >
          {data.map((item, index) => {
            const barHeight = Math.max(4, Math.round((item.value / peak) * CHART_BAR_AREA_PX));
            const dateLabel = formatDate(item.date);
            const tooltipLines = [formatValue(item.value)];
            return (
              <div key={item.date} className="analytics-chart-bar-wrap">
                <ChartColumnTooltip title={dateLabel} lines={tooltipLines}>
                  <div className="analytics-chart-bar-stack" style={{ height: `${CHART_BAR_AREA_PX}px` }}>
                    {showBarValues && item.value > 0 && (
                      <span className="analytics-chart-bar-value">{formatValue(item.value)}</span>
                    )}
                    <span
                      className={`analytics-chart-bar ${barClassName}`}
                      style={{ height: `${barHeight}px`, flex: "none" }}
                    />
                  </div>
                </ChartColumnTooltip>
                <span className="analytics-chart-label admin-stats-day-label">
                  {index % labelStep === 0 ? formatChartDay(item.date) : "\u00a0"}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

export function StatCard({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <article className="card admin-stats-card">
      <p className="admin-stats-card-value">{formatStatValue(value)}</p>
      <p className="admin-stats-card-label">{label}</p>
      {hint && <p className="muted admin-stats-card-hint">{hint}</p>}
    </article>
  );
}
