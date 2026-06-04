import type { ReactNode } from "react";

export function ChartColumnTooltip({
  title,
  lines,
  children,
}: {
  title: string;
  lines: string[];
  children: ReactNode;
}) {
  return (
    <div className="analytics-chart-tooltip-wrap">
      {children}
      <div className="analytics-chart-tooltip" role="tooltip">
        <span className="analytics-chart-tooltip-title">{title}</span>
        {lines.map((line) => (
          <span key={line} className="analytics-chart-tooltip-line">
            {line}
          </span>
        ))}
      </div>
    </div>
  );
}
