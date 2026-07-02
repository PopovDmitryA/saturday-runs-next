import { ChartColumnTooltip } from "./ChartColumnTooltip";
import { pluralizeRu } from "../lib/format";

type FinishTimeDistributionProps = {
  times: number[];
};

type Bin = {
  startSec: number;
  endSec: number;
  count: number;
  hasBest: boolean;
};

function formatMinSec(totalSec: number): string {
  const minutes = Math.floor(totalSec / 60);
  const seconds = Math.round(totalSec % 60);
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function pickBinSizeSec(spreadSec: number): number {
  if (spreadSec <= 6 * 60) {
    return 30;
  }
  if (spreadSec <= 25 * 60) {
    return 60;
  }
  return 120;
}

function buildBins(times: number[]): Bin[] {
  const best = times[0];
  const worst = times[times.length - 1];
  const binSize = pickBinSizeSec(worst - best);
  const firstBinStart = Math.floor(best / binSize) * binSize;
  const binCount = Math.floor((worst - firstBinStart) / binSize) + 1;
  const bins: Bin[] = Array.from({ length: binCount }, (_, index) => ({
    startSec: firstBinStart + index * binSize,
    endSec: firstBinStart + (index + 1) * binSize,
    count: 0,
    hasBest: false,
  }));
  for (const value of times) {
    const bin = bins[Math.floor((value - firstBinStart) / binSize)];
    bin.count += 1;
    if (value === best) {
      bin.hasBest = true;
    }
  }
  return bins;
}

function median(sortedTimes: number[]): number {
  const middle = Math.floor(sortedTimes.length / 2);
  if (sortedTimes.length % 2 === 1) {
    return sortedTimes[middle];
  }
  return Math.round((sortedTimes[middle - 1] + sortedTimes[middle]) / 2);
}

export function FinishTimeDistribution({ times }: FinishTimeDistributionProps) {
  const sorted = [...times].filter((value) => value > 0).sort((a, b) => a - b);
  if (sorted.length < 3) {
    return null;
  }

  const bins = buildBins(sorted);
  const maxCount = Math.max(...bins.map((bin) => bin.count), 1);
  const best = sorted[0];
  const medianSec = median(sorted);
  const avgSec = Math.round(sorted.reduce((sum, value) => sum + value, 0) / sorted.length);
  // Подписи оси: не чаще каждой второй колонки на широких гистограммах
  const labelEvery = bins.length > 16 ? 2 : 1;

  return (
    <div className="analytics-chart finish-dist">
      <div
        className="finish-dist-bars"
        role="img"
        aria-label="Распределение финишных времён"
        style={{ gridTemplateColumns: `repeat(${bins.length}, minmax(0, 1fr))` }}
      >
        {bins.map((bin, index) => (
          <div key={bin.startSec} className="finish-dist-bar-wrap">
            <ChartColumnTooltip
              title={`${formatMinSec(bin.startSec)}–${formatMinSec(bin.endSec)}`}
              lines={[
                pluralizeRu(bin.count, ["пробежка", "пробежки", "пробежек"]),
                ...(bin.hasBest ? [`Лучший результат: ${formatMinSec(best)}`] : []),
              ]}
            >
              <span
                className={`finish-dist-bar${bin.hasBest ? " finish-dist-bar-best" : ""}`}
                style={{ height: `${Math.max((bin.count / maxCount) * 100, bin.count > 0 ? 3 : 0)}%` }}
              />
            </ChartColumnTooltip>
            <span className="analytics-chart-label finish-dist-label">
              {index % labelEvery === 0 ? formatMinSec(bin.startSec) : " "}
            </span>
          </div>
        ))}
      </div>
      <div className="analytics-chart-legend finish-dist-summary">
        <span className="analytics-legend-item">
          <span className="analytics-legend-swatch finish-dist-swatch-best" />
          Лучшее {formatMinSec(best)}
        </span>
        <span className="analytics-legend-item">Медиана {formatMinSec(medianSec)}</span>
        <span className="analytics-legend-item">Среднее {formatMinSec(avgSec)}</span>
        <span className="analytics-legend-item muted">
          {pluralizeRu(sorted.length, ["пробежка", "пробежки", "пробежек"])} с временем
        </span>
      </div>
    </div>
  );
}
