import { useState } from "react";
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

type TimeRange = { startSec: number; endSec: number };

function formatMinSec(totalSec: number): string {
  const minutes = Math.floor(totalSec / 60);
  const seconds = Math.round(totalSec % 60);
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function pickBinSizeSec(spreadSec: number): number {
  if (spreadSec <= 2 * 60) {
    return 10;
  }
  if (spreadSec <= 6 * 60) {
    return 30;
  }
  if (spreadSec <= 25 * 60) {
    return 60;
  }
  return 120;
}

function buildBins(times: number[], globalBest: number): Bin[] {
  const lowest = times[0];
  const worst = times[times.length - 1];
  const binSize = pickBinSizeSec(worst - lowest);
  const firstBinStart = Math.floor(lowest / binSize) * binSize;
  const binCount = Math.floor((worst - firstBinStart) / binSize) + 1;
  const bins: Bin[] = Array.from({ length: binCount }, (_, index) => ({
    startSec: firstBinStart + index * binSize,
    endSec: firstBinStart + (index + 1) * binSize,
    count: 0,
    // Оранжевым помечаем только столбец с реальным лучшим результатом за всё
    // время, а не первый столбец текущего диапазона.
    hasBest: firstBinStart + index * binSize <= globalBest && globalBest < firstBinStart + (index + 1) * binSize,
  }));
  for (const value of times) {
    bins[Math.floor((value - firstBinStart) / binSize)].count += 1;
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
  const [range, setRange] = useState<TimeRange | null>(null);
  const [anchorStart, setAnchorStart] = useState<number | null>(null);

  const sorted = [...times].filter((value) => value > 0).sort((a, b) => a - b);
  if (sorted.length < 3) {
    return null;
  }

  const globalBest = sorted[0];
  const visible = range
    ? sorted.filter((value) => value >= range.startSec && value < range.endSec)
    : sorted;
  const bins = visible.length > 0 ? buildBins(visible, globalBest) : [];
  const maxCount = Math.max(...bins.map((bin) => bin.count), 1);
  const bestInView = visible[0];
  const showsGlobalBest = globalBest >= (range?.startSec ?? -Infinity) && globalBest < (range?.endSec ?? Infinity);
  const labelEvery = bins.length > 16 ? 2 : 1;

  const resetZoom = () => {
    setRange(null);
    setAnchorStart(null);
  };

  const handleBinClick = (bin: Bin) => {
    if (anchorStart === null) {
      setAnchorStart(bin.startSec);
      return;
    }
    const anchorBin = bins.find((item) => item.startSec === anchorStart);
    const startSec = Math.min(anchorStart, bin.startSec);
    const endSec = Math.max(anchorBin?.endSec ?? anchorStart, bin.endSec);
    setRange({ startSec, endSec });
    setAnchorStart(null);
  };

  return (
    <div className="analytics-chart finish-dist">
      <div className="finish-dist-controls">
        {range ? (
          <>
            <span>
              Диапазон {formatMinSec(range.startSec)}–{formatMinSec(range.endSec)}
            </span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={resetZoom}>
              Сбросить
            </button>
          </>
        ) : anchorStart !== null ? (
          <>
            <span>Выберите второй столбик — покажу диапазон подробнее</span>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => setAnchorStart(null)}
            >
              Отмена
            </button>
          </>
        ) : (
          <span className="muted">
            Нажмите на столбик (или два — начало и конец), чтобы приблизить диапазон
          </span>
        )}
      </div>

      {visible.length === 0 ? (
        <p className="muted">В выбранном диапазоне нет пробежек.</p>
      ) : (
        <>
          <div
            className="finish-dist-bars"
            role="img"
            aria-label="Распределение финишных времён"
            style={{ gridTemplateColumns: `repeat(${bins.length}, minmax(0, 1fr))` }}
          >
            {bins.map((bin, index) => (
              <div
                key={bin.startSec}
                className={`finish-dist-bar-wrap${
                  anchorStart === bin.startSec ? " finish-dist-bar-wrap-anchor" : ""
                }`}
                onClick={() => handleBinClick(bin)}
              >
                <ChartColumnTooltip
                  title={`${formatMinSec(bin.startSec)}–${formatMinSec(bin.endSec - 1)}`}
                  lines={[
                    pluralizeRu(bin.count, ["пробежка", "пробежки", "пробежек"]),
                    ...(bin.hasBest ? [`Лучший результат: ${formatMinSec(globalBest)}`] : []),
                  ]}
                >
                  <span
                    className={`finish-dist-bar${bin.hasBest ? " finish-dist-bar-best" : ""}`}
                    style={{
                      height: `${Math.max((bin.count / maxCount) * 100, bin.count > 0 ? 3 : 0)}%`,
                    }}
                  />
                </ChartColumnTooltip>
                <span className="analytics-chart-label finish-dist-label">
                  {index % labelEvery === 0 ? formatMinSec(bin.startSec) : " "}
                </span>
              </div>
            ))}
          </div>
          <div className="analytics-chart-legend finish-dist-summary">
            {showsGlobalBest ? (
              <span className="analytics-legend-item">
                <span className="analytics-legend-swatch finish-dist-swatch-best" />
                Лучшее {formatMinSec(globalBest)}
              </span>
            ) : (
              <span className="analytics-legend-item">
                Быстрейшее в диапазоне {formatMinSec(bestInView)}
              </span>
            )}
            <span className="analytics-legend-item">Медиана {formatMinSec(median(visible))}</span>
            <span className="analytics-legend-item">
              Среднее{" "}
              {formatMinSec(Math.round(visible.reduce((sum, value) => sum + value, 0) / visible.length))}
            </span>
            <span className="analytics-legend-item muted">
              {pluralizeRu(visible.length, ["пробежка", "пробежки", "пробежек"])}
              {range ? " в диапазоне" : " с временем"}
            </span>
          </div>
        </>
      )}
    </div>
  );
}
