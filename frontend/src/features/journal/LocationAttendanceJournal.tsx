import { useEffect, useMemo, useState } from "react";
import { formatDate, formatInt } from "../../lib/format";
import {
  getLocationAttendance,
  type LocationAttendance,
  type LocationAttendanceKind,
  type LocationAttendanceRow,
} from "./attendanceApi";
import {
  AttendanceMatrix,
  type MatrixCell,
  type MatrixRowData,
} from "./AttendanceMatrix";
import "./attendance.css";

// Журнал посещаемости локации: участники × даты стартов площадки. В отличие
// от журналов рейтингов колонки — реальные даты этой площадки, а клетка
// различает пробежку, волонтёрство и «и то и другое». Внизу — строка
// «Участников»: посещаемость каждого старта читается прямо из журнала.

type LocationAttendanceJournalProps = {
  slug: string;
};

const KIND_TABS: { value: LocationAttendanceKind; label: string }[] = [
  { value: "runners", label: "Бегуны" },
  { value: "all", label: "Все" },
  { value: "volunteers", label: "Волонтёры" },
];

function cellFor(row: LocationAttendanceRow): Record<string, MatrixCell> {
  const cells: Record<string, MatrixCell> = {};
  for (const item of row.items) {
    const hasVol = item.roles.length > 0;
    const kind: MatrixCell["kind"] = item.run && hasVol ? "both" : hasVol ? "vol" : "run";
    const lines: string[] = [];
    if (item.run) {
      lines.push("пробежка");
    }
    if (hasVol) {
      lines.push(`волонтёрство: ${item.roles.join(", ")}`);
    }
    cells[item.date] = {
      kind,
      count: 1,
      tooltipTitle: formatDate(item.date),
      tooltipLines: lines,
    };
  }
  return cells;
}

function matrixRow(
  row: LocationAttendanceRow,
  index: number,
  me = false,
): MatrixRowData {
  const name = row.name?.trim() || "Участник";
  return {
    id: me ? "me" : `${index}-${row.handle ?? name}`,
    rank: me ? null : index + 1,
    name: row.handle ? (
      <a className="ajm-name-link" href={`/users/${row.handle}`}>
        {me ? `${name} (вы)` : name}
      </a>
    ) : me ? (
      `${name} (вы)`
    ) : (
      name
    ),
    total: row.year_total,
    me,
    private: row.private && !me,
    cells: cellFor(row),
  };
}

export function LocationAttendanceJournal({ slug }: LocationAttendanceJournalProps) {
  const [year, setYear] = useState<number | null>(null);
  const [kind, setKind] = useState<LocationAttendanceKind>("all");
  const [data, setData] = useState<LocationAttendance | null>(null);
  const [extraRows, setExtraRows] = useState<LocationAttendanceRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getLocationAttendance(slug, { year, kind })
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
          setExtraRows([]);
        }
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
  }, [slug, year, kind]);

  const loadMore = () => {
    if (!data) {
      return;
    }
    setLoadingMore(true);
    getLocationAttendance(slug, {
      year: data.year,
      kind,
      offset: data.rows.length + extraRows.length,
    })
      .then((payload) => setExtraRows((rows) => [...rows, ...payload.rows]))
      .catch(() => setError("Не удалось загрузить продолжение журнала."))
      .finally(() => setLoadingMore(false));
  };

  const columns = useMemo(
    () =>
      (data?.columns ?? []).map((column) => ({
        key: column.date,
        label: formatDate(column.date).slice(0, 5),
      })),
    [data],
  );

  const rows = useMemo(() => {
    if (!data) {
      return [];
    }
    const listed = [...data.rows, ...extraRows];
    const result = listed.map((row, index) => matrixRow(row, index));
    if (data.me && data.me.year_total > 0) {
      // Свою строку закрепляем сверху; из общего списка её не вычёркиваем —
      // там она стоит на своём месте по счёту года.
      result.unshift(matrixRow(data.me, -1, true));
    }
    return result;
  }, [data, extraRows]);

  // Итоговая строка следует срезу: в «Бегунах» считает бегунов, в
  // «Волонтёрах» — волонтёров, в «Все» — и тех, и других.
  const totals = useMemo(() => {
    if (!data) {
      return undefined;
    }
    const values: Record<string, number> = {};
    for (const [date, counters] of Object.entries(data.date_totals)) {
      values[date] =
        kind === "runners"
          ? counters.runners
          : kind === "volunteers"
            ? counters.volunteers
            : counters.runners + counters.volunteers;
    }
    const label =
      kind === "runners" ? "Бегунов" : kind === "volunteers" ? "Волонтёров" : "Участников";
    return {
      label,
      values,
      title: `${label} на этом старте — по всем за год, не только по видимым строкам`,
    };
  }, [data, kind]);

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
        <div className="aj-tabs" role="group" aria-label="Кого показывать">
          {KIND_TABS.map((tab) => (
            <button
              key={tab.value}
              type="button"
              aria-pressed={kind === tab.value}
              className={`aj-tab${kind === tab.value ? " aj-tab-active" : ""}`}
              onClick={() => setKind(tab.value)}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Легенда — под выбранный срез: в «Бегунах» синих клеток не бывает
          вовсе, и обещать их в легенде незачем. */}
      <div className="aj-legend">
        {kind !== "volunteers" && (
          <span>
            <i className="ajm-swatch ajm-dot-run" />
            пробежка
          </span>
        )}
        {kind !== "runners" && (
          <span>
            <i className="ajm-swatch ajm-dot-vol" />
            волонтёрство
          </span>
        )}
        {kind === "all" && (
          <span>
            <i className="ajm-swatch ajm-dot-both" />
            и то и другое
          </span>
        )}
        <span>
          <i className="ajm-swatch" />
          пропуск
        </span>
      </div>

      {error && <p className="aj-error">{error}</p>}
      {loading && !data && <p className="muted">Собираем журнал…</p>}
      {data && (
        <div className={loading ? "aj-refreshing" : undefined}>
          <AttendanceMatrix
            columns={columns}
            rows={rows}
            totalLabel={
              kind === "runners" ? "Пробежек" : kind === "volunteers" ? "Волонтёрств" : "Всего"
            }
            totalHint={
              kind === "all"
                ? "Дней активности в выбранном году: день с пробежкой и волонтёрством считается одним днём"
                : kind === "runners"
                  ? "Пробежек на этой площадке в выбранном году"
                  : "Волонтёрств на этой площадке в выбранном году"
            }
            totals={totals}
            emptyNote="За этот год стартов не было."
          />
          <p className="aj-meta muted">
            Показаны {formatInt(Math.min(shownRows, data.total_rows))} из{" "}
            {formatInt(data.total_rows)}{" "}
            {kind === "volunteers" ? "волонтёров" : kind === "runners" ? "бегунов" : "участников"}
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
