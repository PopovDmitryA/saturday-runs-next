import { useCallback, useRef, type ReactNode } from "react";
import { ChartColumnTooltip } from "../../components/ChartColumnTooltip";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { TableWrap } from "../../components/tableUx/TableWrap";

// Матрица журнала посещаемости: строки — участники, колонки — даты (свежие
// слева), клетка — факт посещения. Общий вид для всех четырёх журналов:
// пробежки, волонтёрства, туризм и журнал локации — меняется только смысл
// цвета клетки. Первые колонки липкие, даты уезжают в горизонтальный скролл
// с тенями (TableWrap), наведение/тап подсвечивает столбец.

export type MatrixCellKind = "run" | "vol" | "both" | "new" | "repeat";

export type MatrixCell = {
  kind: MatrixCellKind;
  // Отметок в клетке: > 1 показывается цифрой (два старта за неделю).
  count: number;
  // Заголовок и строки быстрой подсказки (ChartColumnTooltip — та же, что у
  // календаря-хитмэпа ЛК: всплывает по :hover без задержки нативного title).
  tooltipTitle: string;
  tooltipLines: string[];
};

export type MatrixColumn = {
  key: string;
  label: string;
};

export type MatrixRowData = {
  id: string;
  rank: number | null;
  name: ReactNode;
  /** Имя простым текстом — для поиска и сортировки: name может быть ссылкой. */
  searchName?: string;
  total: number;
  /** Счёт по месяцам «YYYY-MM» → участия: журнал локации подставляет его в срезе месяца. */
  monthTotals?: Record<string, number>;
  me?: boolean;
  // Закрытый профиль: клетки не показываем, счёт года остаётся.
  private?: boolean;
  cells: Record<string, MatrixCell | undefined>;
};

type MatrixTotals = {
  label: string;
  values: Record<string, number | undefined>;
  title?: string;
};

export type MatrixSortKey = "name" | "total";

type AttendanceMatrixProps = {
  columns: MatrixColumn[];
  rows: MatrixRowData[];
  /** Подпись колонки счёта («Всего», «Новых», …). */
  totalLabel: string;
  totalHint?: string;
  /** Итоговая строка по датам (журнал локации: «Участников»). */
  totals?: MatrixTotals;
  /** ISO-месяцы для группирующей строки берём из ключа колонки (YYYY-MM-DD). */
  emptyNote?: string;
  /**
   * Сортировка по «Участнику» и «Всего». Передаётся вместе с onSort: без
   * обработчика заголовки остаются обычными, чтобы журнал рейтингов, где
   * порядок задаёт сервер, не получил нерабочих стрелок.
   */
  sort?: { key: MatrixSortKey; asc: boolean } | null;
  onSort?: (key: MatrixSortKey) => void;
};

const MONTH_LABELS = [
  "янв",
  "фев",
  "мар",
  "апр",
  "май",
  "июн",
  "июл",
  "авг",
  "сен",
  "окт",
  "ноя",
  "дек",
];

function monthOf(columnKey: string): number {
  const match = /^\d{4}-(\d{2})/.exec(columnKey);
  return match ? Number(match[1]) - 1 : -1;
}

/** Группы соседних колонок одного месяца — для строки-шапки с подписями. */
function monthGroups(columns: MatrixColumn[]): { label: string; span: number }[] {
  const groups: { label: string; span: number }[] = [];
  for (const column of columns) {
    const month = monthOf(column.key);
    const label = month >= 0 ? MONTH_LABELS[month] : "";
    const last = groups[groups.length - 1];
    if (last && last.label === label) {
      last.span += 1;
    } else {
      groups.push({ label, span: 1 });
    }
  }
  return groups;
}

export function AttendanceMatrix({
  columns,
  rows,
  totalLabel,
  totalHint,
  totals,
  emptyNote,
  sort = null,
  onSort,
}: AttendanceMatrixProps) {
  const tableRef = useRef<HTMLTableElement | null>(null);

  // Подсветка столбца — прямыми классами, без React-состояния: перерисовывать
  // сотни клеток на каждое движение мыши незачем.
  const highlightColumn = useCallback((index: number | null) => {
    const table = tableRef.current;
    if (!table) {
      return;
    }
    table.querySelectorAll(".ajm-hl").forEach((cell) => cell.classList.remove("ajm-hl"));
    if (index === null) {
      return;
    }
    table
      .querySelectorAll(`[data-col="${index}"]`)
      .forEach((cell) => cell.classList.add("ajm-hl"));
  }, []);

  const onOver = useCallback(
    (event: { target: EventTarget }) => {
      const cell = (event.target as HTMLElement).closest("[data-col]");
      highlightColumn(cell ? Number((cell as HTMLElement).dataset.col) : null);
    },
    [highlightColumn],
  );

  if (columns.length === 0 || rows.length === 0) {
    return <p className="muted">{emptyNote ?? "За этот год отметок нет."}</p>;
  }

  const groups = monthGroups(columns);
  const monthStarts = new Set<number>();
  let offset = 0;
  for (const group of groups) {
    monthStarts.add(offset);
    offset += group.span;
  }

  return (
    <TableWrap className="ajm-wrap">
      <table
        ref={tableRef}
        className="ajm-table"
        onMouseOver={onOver}
        onMouseLeave={() => highlightColumn(null)}
      >
        <thead>
          <tr className="ajm-months-row">
            <th className="ajm-col-rank" aria-hidden />
            <th className="ajm-col-name" aria-hidden />
            <th className="ajm-col-total" aria-hidden />
            {groups.map((group, index) => (
              <th key={`${group.label}-${index}`} colSpan={group.span} className="ajm-month">
                {group.label}
              </th>
            ))}
          </tr>
          <tr>
            <th className="ajm-col-rank">#</th>
            {onSort ? (
              <ColumnHeader
                label="Участник"
                className="ajm-col-name"
                filterable={false}
                sortActive={sort?.key === "name"}
                sortAsc={sort?.key === "name" ? sort.asc : true}
                onSort={() => onSort("name")}
              />
            ) : (
              <th className="ajm-col-name">Участник</th>
            )}
            {onSort ? (
              <ColumnHeader
                label={totalLabel}
                className="ajm-col-total"
                filterable={false}
                hint={totalHint}
                sortActive={sort?.key === "total"}
                sortAsc={sort?.key === "total" ? sort.asc : false}
                onSort={() => onSort("total")}
              />
            ) : (
              <th
                className="ajm-col-total"
                title={totalHint}
                data-tap-tooltip={totalHint ? undefined : "off"}
              >
                {totalLabel}
              </th>
            )}
            {columns.map((column, index) => (
              <th
                key={column.key}
                className={`ajm-col-date${monthStarts.has(index) ? " ajm-month-start" : ""}`}
                data-col={index}
              >
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className={row.me ? "ajm-me" : undefined}>
              <td className="ajm-col-rank">{row.rank ?? "—"}</td>
              <td className="ajm-col-name">
                {row.name}
                {row.private && (
                  <span
                    className="ajm-private"
                    title="Профиль закрыт — отметки по датам скрыты"
                    aria-label="Профиль закрыт — отметки по датам скрыты"
                  >
                    🔒
                  </span>
                )}
              </td>
              <td className="ajm-col-total">{row.total}</td>
              {columns.map((column, index) => {
                const cell = row.private ? undefined : row.cells[column.key];
                // Подсказка центрирована над клеткой — у первых/последних
                // колонок центр съезжал бы за край скролл-контейнера, поэтому
                // возле краёв она прижимается к своему краю клетки (см. CSS
                // ajm-table td[data-edge]).
                const edge = index < 3 ? "start" : index >= columns.length - 3 ? "end" : undefined;
                return (
                  <td
                    key={column.key}
                    className={`ajm-col-date${monthStarts.has(index) ? " ajm-month-start" : ""}`}
                    data-col={index}
                    data-edge={edge}
                  >
                    {cell ? (
                      <ChartColumnTooltip title={cell.tooltipTitle} lines={cell.tooltipLines}>
                        <span className={`ajm-dot ajm-dot-${cell.kind}`}>
                          {cell.count > 1 ? cell.count : ""}
                        </span>
                      </ChartColumnTooltip>
                    ) : (
                      <span className="ajm-dot" />
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
        {totals && (
          <tfoot>
            <tr>
              <td className="ajm-col-rank" />
              <td
                className="ajm-col-name ajm-totals-label"
                title={totals.title}
                data-tap-tooltip={totals.title ? undefined : "off"}
              >
                {totals.label}
              </td>
              <td className="ajm-col-total" />
              {columns.map((column, index) => (
                <td key={column.key} className="ajm-col-date ajm-totals" data-col={index}>
                  {totals.values[column.key] ?? ""}
                </td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
    </TableWrap>
  );
}
