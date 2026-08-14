import { useMemo, useState } from "react";
import { ChartColumnTooltip } from "../../components/ChartColumnTooltip";
import type { LocationHistogramRow } from "../../lib/api";
import { formatInt, pluralizeRu } from "../../lib/format";

type LocationFinishHistogramProps = {
  rows: LocationHistogramRow[];
  binSizeSec: number;
};

type DisplayBin = {
  startSec: number;
  endSec: number;
  total: number;
  male: number;
  female: number;
  unknown: number;
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

export function LocationFinishHistogram({ rows, binSizeSec }: LocationFinishHistogramProps) {
  const [range, setRange] = useState<TimeRange | null>(null);
  const [anchorStart, setAnchorStart] = useState<number | null>(null);

  const visibleRows = useMemo(
    () => (range ? rows.filter((row) => row.start_sec >= range.startSec && row.start_sec < range.endSec) : rows),
    [rows, range],
  );

  const bins = useMemo(() => {
    if (visibleRows.length === 0) {
      return [] as DisplayBin[];
    }
    const minStart = Math.min(...visibleRows.map((row) => row.start_sec));
    const maxStart = Math.max(...visibleRows.map((row) => row.start_sec));
    const displayBinSize = Math.max(pickBinSizeSec(maxStart + binSizeSec - minStart), binSizeSec);
    const firstBinStart = Math.floor(minStart / displayBinSize) * displayBinSize;
    const binCount = Math.floor((maxStart - firstBinStart) / displayBinSize) + 1;
    const result: DisplayBin[] = Array.from({ length: binCount }, (_, index) => ({
      startSec: firstBinStart + index * displayBinSize,
      endSec: firstBinStart + (index + 1) * displayBinSize,
      total: 0,
      male: 0,
      female: 0,
      unknown: 0,
    }));
    for (const row of visibleRows) {
      const bin = result[Math.floor((row.start_sec - firstBinStart) / displayBinSize)];
      bin.total += row.count;
      if (row.gender === "male") {
        bin.male += row.count;
      } else if (row.gender === "female") {
        bin.female += row.count;
      } else {
        bin.unknown += row.count;
      }
    }
    return result;
  }, [visibleRows, binSizeSec]);

  const totals = useMemo(() => {
    let total = 0;
    let male = 0;
    let female = 0;
    let weightedSum = 0;
    for (const bin of bins) {
      total += bin.total;
      male += bin.male;
      female += bin.female;
      weightedSum += (bin.startSec + (bin.endSec - bin.startSec) / 2) * bin.total;
    }
    let median: number | null = null;
    let cumulative = 0;
    for (const bin of bins) {
      cumulative += bin.total;
      if (cumulative >= total / 2) {
        median = bin.startSec + (bin.endSec - bin.startSec) / 2;
        break;
      }
    }
    return {
      total,
      male,
      female,
      mean: total > 0 ? Math.round(weightedSum / total) : null,
      median: median !== null ? Math.round(median) : null,
    };
  }, [bins]);

  if (rows.length === 0) {
    return <p className="muted">Пока нет протоколов с финишными временами.</p>;
  }

  const maxCount = Math.max(...bins.map((bin) => bin.total), 1);
  const labelEvery = bins.length > 16 ? 2 : 1;

  const resetZoom = () => {
    setRange(null);
    setAnchorStart(null);
  };

  const handleBinClick = (bin: DisplayBin) => {
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

  const segmentStyle = (count: number, bin: DisplayBin) => ({
    height: `${(count / Math.max(bin.total, 1)) * 100}%`,
  });

  return (
    <div className="analytics-chart finish-dist loc-hist">
      <div className="finish-dist-controls">
        {range ? (
          <>
            <span>
              Диапазон {formatMinSec(range.startSec)}–{formatMinSec(range.endSec - 1)}
            </span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={resetZoom}>
              Сбросить
            </button>
          </>
        ) : anchorStart !== null ? (
          <>
            <span>Выберите второй столбик — покажу диапазон подробнее</span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setAnchorStart(null)}>
              Отмена
            </button>
          </>
        ) : (
          <span className="muted">
            Нажмите на столбик (или два — начало и конец), чтобы приблизить диапазон
          </span>
        )}
      </div>

      {bins.length === 0 ? (
        <p className="muted">В выбранном диапазоне нет финишей.</p>
      ) : (
        <>
          <div
            className="finish-dist-bars"
            role="img"
            aria-label="Распределение финишных времён на локации"
            style={{ gridTemplateColumns: `repeat(${bins.length}, minmax(0, 1fr))` }}
          >
            {bins.map((bin, index) => (
              <div
                key={bin.startSec}
                className={`finish-dist-bar-wrap${
                  anchorStart === bin.startSec ? " finish-dist-bar-wrap-anchor" : ""
                }`}
                onClick={() => handleBinClick(bin)}
                // Тап по столбцу задаёт диапазон — подсказку тач-режимом не открываем.
                data-tap-tooltip="off"
              >
                <ChartColumnTooltip
                  title={`${formatMinSec(bin.startSec)}–${formatMinSec(bin.endSec - 1)}`}
                  lines={[
                    pluralizeRu(bin.total, ["финиш", "финиша", "финишей"]),
                    `Мужчины: ${formatInt(bin.male)}`,
                    `Женщины: ${formatInt(bin.female)}`,
                    ...(bin.unknown > 0 ? [`Пол неизвестен: ${bin.unknown}`] : []),
                  ]}
                >
                  <span
                    className="loc-hist-bar"
                    style={{
                      height: `${Math.max((bin.total / maxCount) * 100, bin.total > 0 ? 3 : 0)}%`,
                    }}
                  >
                    {bin.unknown > 0 && (
                      <span className="loc-hist-seg loc-hist-seg-unknown" style={segmentStyle(bin.unknown, bin)} />
                    )}
                    {bin.female > 0 && (
                      <span className="loc-hist-seg loc-hist-seg-female" style={segmentStyle(bin.female, bin)} />
                    )}
                    {bin.male > 0 && (
                      <span className="loc-hist-seg loc-hist-seg-male" style={segmentStyle(bin.male, bin)} />
                    )}
                  </span>
                </ChartColumnTooltip>
                <span className="analytics-chart-label finish-dist-label">
                  {index % labelEvery === 0 ? formatMinSec(bin.startSec) : " "}
                </span>
              </div>
            ))}
          </div>
          <div className="analytics-chart-legend finish-dist-summary">
            <span className="analytics-legend-item">
              <span className="analytics-legend-swatch loc-hist-swatch-male" />
              Мужчины {formatInt(totals.male)}
            </span>
            <span className="analytics-legend-item">
              <span className="analytics-legend-swatch loc-hist-swatch-female" />
              Женщины {formatInt(totals.female)}
            </span>
            {totals.median !== null && (
              <span className="analytics-legend-item">Медиана ~{formatMinSec(totals.median)}</span>
            )}
            {totals.mean !== null && (
              <span className="analytics-legend-item">Среднее ~{formatMinSec(totals.mean)}</span>
            )}
            <span className="analytics-legend-item muted">
              {pluralizeRu(totals.total, ["финиш", "финиша", "финишей"])}
              {range ? " в диапазоне" : " с временем в протоколах"}
            </span>
          </div>
        </>
      )}
    </div>
  );
}
