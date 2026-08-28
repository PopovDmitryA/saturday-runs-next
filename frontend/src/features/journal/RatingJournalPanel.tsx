import { useEffect, useMemo, useState } from "react";
import { formatDate, formatInt, platformCodeLabel } from "../../lib/format";
import {
  getAttendanceJournal,
  type AttendanceJournal,
  type JournalItem,
  type JournalMetric,
  type JournalRow,
} from "./attendanceApi";
import {
  AttendanceMatrix,
  type MatrixCell,
  type MatrixColumn,
  type MatrixRowData,
} from "./AttendanceMatrix";
import "./attendance.css";

// Режим «Журнал» страницы рейтинга: те же строки и порядок, что в таблице,
// но вместо колонок систем — клетки по неделям выбранного года. Свежие недели
// слева: первый экран без скролла показывает срез последних недель.

type RatingJournalPanelProps = {
  metric: JournalMetric;
  platform: string;
  platformOptions: string[];
  onPlatformChange: (platform: string) => void;
};

const TOTAL_LABELS: Record<JournalMetric, string> = {
  runs: "За год",
  volunteering: "За год",
  locations: "Новых",
};

const TOTAL_HINTS: Record<JournalMetric, string> = {
  runs: "Пробежек в выбранном году",
  volunteering: "Волонтёрств в выбранном году",
  locations:
    "Новых площадок в выбранном году: сумма по годам сходится с общим зачётом рейтинга",
};

/** Суббота, закрывающая неделю (вс–сб) с этой датой — та же логика, что в
 * календаре-хитмэпе «Пробежек» личного кабинета: воскресный RunPark попадает
 * в клетку ближайшей следующей субботы. */
function weekSaturdayIso(isoDate: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(isoDate);
  if (!match) {
    return isoDate;
  }
  const value = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  value.setDate(value.getDate() + (6 - value.getDay()));
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${value.getFullYear()}-${month}-${day}`;
}

function saturdayColumns(year: number): MatrixColumn[] {
  const today = new Date();
  const current = new Date(year, 0, 1);
  current.setDate(current.getDate() + ((6 - current.getDay()) % 7));
  const columns: MatrixColumn[] = [];
  while (current.getFullYear() === year) {
    // Будущие недели не показываем: пустой правый хвост года — просто шум.
    if (current.getTime() - today.getTime() > 6 * 24 * 3600 * 1000) {
      break;
    }
    const month = String(current.getMonth() + 1).padStart(2, "0");
    const day = String(current.getDate()).padStart(2, "0");
    columns.push({
      key: `${year}-${month}-${day}`,
      label: `${day}.${month}`,
    });
    current.setDate(current.getDate() + 7);
  }
  // Свежие даты — слева, сразу у липких колонок.
  columns.reverse();
  return columns;
}

function itemLine(metric: JournalMetric, item: JournalItem): string {
  const parts = [formatDate(item.date), item.location ?? "", platformCodeLabel(item.platform)];
  if (metric === "volunteering" && item.role) {
    parts.push(item.role);
  }
  if (metric === "locations") {
    parts.push(item.new ? "новая локация" : "повтор");
  }
  return parts.filter(Boolean).join(" · ");
}

function rowCells(metric: JournalMetric, row: JournalRow): Record<string, MatrixCell> {
  const byWeek = new Map<string, JournalItem[]>();
  for (const item of row.items) {
    const key = weekSaturdayIso(item.date);
    const bucket = byWeek.get(key);
    if (bucket) {
      bucket.push(item);
    } else {
      byWeek.set(key, [item]);
    }
  }
  const cells: Record<string, MatrixCell> = {};
  for (const [key, items] of byWeek) {
    let kind: MatrixCell["kind"] = "run";
    if (metric === "volunteering") {
      kind = "vol";
    } else if (metric === "locations") {
      kind = items.some((item) => item.new) ? "new" : "repeat";
    }
    cells[key] = {
      kind,
      count: items.length,
      tooltipTitle: formatDate(key),
      tooltipLines: items.map((item) => itemLine(metric, item)),
    };
  }
  return cells;
}

function matrixRow(metric: JournalMetric, row: JournalRow, me = false): MatrixRowData {
  const name = row.display_name?.trim() || "Участник";
  return {
    id: me ? `me-${row.row_key}` : row.row_key,
    rank: row.rank,
    name:
      row.site_serial_id != null ? (
        <a className="ajm-name-link" href={`/users/${row.site_serial_id}`}>
          {me ? `${name} (вы)` : name}
        </a>
      ) : me ? (
        `${name} (вы)`
      ) : (
        name
      ),
    total: row.year_total,
    me,
    private: row.private,
    cells: rowCells(metric, row),
  };
}

export function RatingJournalPanel({
  metric,
  platform,
  platformOptions,
  onPlatformChange,
}: RatingJournalPanelProps) {
  const [year, setYear] = useState<number | null>(null);
  const [data, setData] = useState<AttendanceJournal | null>(null);
  const [extraRows, setExtraRows] = useState<JournalRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getAttendanceJournal(metric, { year, platform })
      .then((payload) => {
        if (cancelled) {
          return;
        }
        setData(payload);
        setExtraRows([]);
      })
      .catch(() => {
        if (!cancelled) {
          setError("Не удалось загрузить журнал. Попробуйте обновить страницу.");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [metric, year, platform]);

  const loadMore = () => {
    if (!data) {
      return;
    }
    setLoadingMore(true);
    getAttendanceJournal(metric, {
      year: data.year,
      platform,
      offset: data.rows.length + extraRows.length,
    })
      .then((payload) => setExtraRows((rows) => [...rows, ...payload.rows]))
      .catch(() => setError("Не удалось загрузить продолжение журнала."))
      .finally(() => setLoadingMore(false));
  };

  const columns = useMemo(
    () => (data ? saturdayColumns(data.year) : []),
    [data],
  );

  const rows = useMemo(() => {
    if (!data) {
      return [];
    }
    const listed = [...data.rows, ...extraRows];
    const result = listed.map((row) => matrixRow(metric, row));
    // Строка «Вы» закреплена сверху; если зритель и так на странице —
    // дублировать её не нужно, только подсветить.
    if (data.me) {
      const meKey = data.me.row_key;
      const existing = result.find((row) => row.id === meKey);
      if (existing) {
        existing.me = true;
      } else if (data.me.year_total > 0) {
        result.unshift(matrixRow(metric, data.me, true));
      }
    }
    return result;
  }, [data, extraRows, metric]);

  const shownRows = data ? data.rows.length + extraRows.length : 0;
  const hasMore = data ? shownRows < data.total_rows : false;

  return (
    <div className="aj-panel">
      <div className="aj-controls">
        {data && data.years.length > 1 && (
          <div className="aj-tabs aj-years" role="group" aria-label="Год">
            {data.years.map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={value === data.year}
                className={`aj-tab${value === data.year ? " aj-tab-active" : ""}`}
                onClick={() => setYear(value)}
              >
                {value}
              </button>
            ))}
          </div>
        )}
        {platformOptions.length > 1 && (
          <div className="aj-tabs" role="group" aria-label="Система">
            {platformOptions.map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={platform === value}
                className={`aj-tab${platform === value ? " aj-tab-active" : ""}`}
                onClick={() => onPlatformChange(value)}
              >
                {value === "all" ? "Все" : platformCodeLabel(value)}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="aj-legend">
        {metric === "runs" && (
          <>
            <span>
              <i className="ajm-swatch ajm-dot-run" />
              пробежка
            </span>
            <span>
              <i className="ajm-swatch" />
              пропуск
            </span>
            <span className="muted">цифра в клетке — два и больше старта за неделю</span>
          </>
        )}
        {metric === "volunteering" && (
          <>
            <span>
              <i className="ajm-swatch ajm-dot-vol" />
              волонтёрство
            </span>
            <span>
              <i className="ajm-swatch" />
              пропуск
            </span>
          </>
        )}
        {metric === "locations" && (
          <>
            <span>
              <i className="ajm-swatch ajm-dot-new" />
              новая локация
            </span>
            <span>
              <i className="ajm-swatch ajm-dot-repeat" />
              повтор
            </span>
            <span>
              <i className="ajm-swatch" />
              пропуск
            </span>
          </>
        )}
      </div>

      {error && <p className="aj-error">{error}</p>}
      {loading && !data && <p className="muted">Собираем журнал…</p>}
      {data && (
        <div className={loading ? "aj-refreshing" : undefined}>
          <AttendanceMatrix
            columns={columns}
            rows={rows}
            totalLabel={TOTAL_LABELS[metric]}
            totalHint={TOTAL_HINTS[metric]}
            emptyNote="За этот год отметок у верхушки рейтинга нет."
          />
          <p className="aj-meta muted">
            Показаны {formatInt(Math.min(shownRows, data.total_rows))} из{" "}
            {formatInt(data.total_rows)} строк рейтинга
            {metric === "volunteering" &&
              " · волонтёрства parkrun в журнале не показываются: у них нет даты"}
          </p>
          {hasMore && (
            <div className="aj-more">
              <button type="button" className="btn" onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? "Загружаем…" : "Показать ещё"}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
