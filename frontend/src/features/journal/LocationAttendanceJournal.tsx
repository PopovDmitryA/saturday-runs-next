import { useEffect, useMemo, useState, type ReactNode } from "react";
import { formatDate, formatInt } from "../../lib/format";
import {
  getLocationAttendance,
  type LocationAttendance,
  type LocationAttendanceKind,
  type LocationAttendanceRow,
} from "./attendanceApi";
import {
  FilterGroup,
  FilterPanel,
  FilterRow,
  FilterSearch,
  FilterSelect,
} from "../../components/filters/FilterPanel";
import {
  AttendanceMatrix,
  type MatrixCell,
  type MatrixRowData,
  type MatrixSortKey,
} from "./AttendanceMatrix";
import { surnameFirst } from "../../lib/personName";
import "./attendance.css";

// Журнал посещаемости локации: участники × даты стартов площадки. В отличие
// от журналов рейтингов колонки — реальные даты этой площадки, а клетка
// различает пробежку, волонтёрство и «и то и другое». Внизу — строка
// «Участников»: посещаемость каждого старта читается прямо из журнала.

type LocationAttendanceJournalProps = {
  slug: string;
  /** Переключатель «Протоколы | Посещаемость» — первый ряд общей панели. */
  viewTabs?: ReactNode;
};

// Полными словами, а не «янв»: в выпадающем списке сокращения читаются хуже,
// чем в шапке таблицы, где место дороже.
const MONTH_LABELS = [
  "Январь",
  "Февраль",
  "Март",
  "Апрель",
  "Май",
  "Июнь",
  "Июль",
  "Август",
  "Сентябрь",
  "Октябрь",
  "Ноябрь",
  "Декабрь",
];

// «Все» первым — как во всех остальных фильтрах сайта («Все / 5 вёрст / С95…»).
const KIND_TABS: { value: LocationAttendanceKind; label: string }[] = [
  { value: "all", label: "Все" },
  { value: "runners", label: "Бегуны" },
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
  // С фамилии, как в рейтингах: столбец читается и сортируется как список.
  const name = surnameFirst(row.name) || "Участник";
  return {
    id: me ? "me" : `${index}-${row.handle ?? name}`,
    rank: me ? null : index + 1,
    searchName: name,
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

export function LocationAttendanceJournal({ slug, viewTabs }: LocationAttendanceJournalProps) {
  const [year, setYear] = useState<number | null>(null);
  // Месяц сужает видимые колонки дат: за год их набирается полсотни, и найти
  // конкретную субботу глазами тяжело. Счёт в «Всего» остаётся годовым —
  // так и подписан.
  const [month, setMonth] = useState<string>("all");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: MatrixSortKey; asc: boolean } | null>(null);
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

  // Год сменился — прежний месяц к новому набору колонок отношения не имеет.
  useEffect(() => {
    setMonth("all");
  }, [year, slug]);

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

  // Месяцы, в которых у площадки были старты, — только они и предлагаются.
  const monthOptions = useMemo(() => {
    const seen = new Set<string>();
    for (const column of data?.columns ?? []) {
      seen.add(column.date.slice(0, 7));
    }
    return [...seen]
      .sort()
      .map((value) => ({ value, label: MONTH_LABELS[Number(value.slice(5, 7)) - 1] }));
  }, [data]);

  const columns = useMemo(
    () =>
      (data?.columns ?? [])
        .filter((column) => month === "all" || column.date.slice(0, 7) === month)
        .map((column) => ({
          key: column.date,
          label: formatDate(column.date).slice(0, 5),
        })),
    [data, month],
  );

  const rows = useMemo(() => {
    if (!data) {
      return [];
    }
    const listed = [...data.rows, ...extraRows];
    const result = listed.map((row, index) => matrixRow(row, index));
    // Своя строка сверху — только если зрителя ещё не видно в загруженном
    // куске. Если он уже в списке, дублировать его незачем: раньше человек
    // из топа встречал себя дважды — закреплённым и на своём месте
    // (Дмитрий 02.09.2026). Тот же приём, что в журнале рейтингов.
    if (data.me && data.me.year_total > 0) {
      const meHandle = data.me.handle;
      const existing = meHandle
        ? result.find((row) => row.id.endsWith(`-${meHandle}`))
        : undefined;
      if (existing) {
        existing.me = true;
        existing.name = matrixRow(data.me, -1, true).name;
      } else {
        result.unshift(matrixRow(data.me, -1, true));
      }
    }
    return result;
  }, [data, extraRows]);

  // Поиск и сортировка идут по уже загруженным строкам: сервер отдаёт журнал
  // порциями, и «Показать ещё» подтягивает следующие. Об этом честно написано
  // под таблицей — «Показаны N из M».
  const visibleRows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = needle
      ? rows.filter((row) => (row.searchName ?? "").toLowerCase().includes(needle))
      : rows;
    if (!sort) {
      return filtered;
    }
    const sorted = [...filtered].sort((a, b) => {
      if (sort.key === "name") {
        return (a.searchName ?? "").localeCompare(b.searchName ?? "", "ru");
      }
      return a.total - b.total;
    });
    if (!sort.asc) {
      sorted.reverse();
    }
    return sorted;
  }, [rows, query, sort]);

  const toggleSort = (key: MatrixSortKey) => {
    setSort((current) => {
      if (current?.key !== key) {
        // Имя — от А, счёт — от большего: так их и читают.
        return { key, asc: key === "name" };
      }
      return current.asc ? { key, asc: false } : null;
    });
  };

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
            : // Люди без повторов: кто бежал и волонтёрил в один день — один
              // человек. Сумма — фолбэк для кэшированных ответов без поля.
              counters.people || counters.runners + counters.volunteers;
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
      {/* Та же панель, что у остальных витрин: вид, год и зачёт одной рамкой. */}
      <FilterPanel>
        <FilterRow>
        {viewTabs && <FilterGroup label="Вид">{viewTabs}</FilterGroup>}
        {data && data.years.length > 1 && (
          <FilterGroup label="Год">
            <FilterSelect
              ariaLabel="Год"
              value={data.year}
              onChange={(value) => setYear(value)}
              options={data.years.map((value) => ({ value, label: String(value) }))}
            />
          </FilterGroup>
        )}
        {monthOptions.length > 1 && (
          <FilterGroup label="Месяц">
            <FilterSelect
              ariaLabel="Месяц"
              value={month}
              onChange={(value) => setMonth(String(value))}
              options={[{ value: "all", label: "Все" }, ...monthOptions]}
            />
          </FilterGroup>
        )}
        <FilterGroup label="Зачёт">
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
        </FilterGroup>
        <FilterSearch
          value={query}
          onChange={setQuery}
          ariaLabel="Поиск участника в журнале"
        />
        </FilterRow>
      </FilterPanel>

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
            rows={visibleRows}
            sort={sort}
            onSort={toggleSort}
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
            {query.trim() ? (
              <>
                Найдено {formatInt(visibleRows.length)} среди загруженных{" "}
                {formatInt(Math.min(shownRows, data.total_rows))} из{" "}
                {formatInt(data.total_rows)} участников
              </>
            ) : (
              <>
                Показаны {formatInt(Math.min(shownRows, data.total_rows))} из{" "}
                {formatInt(data.total_rows)} участников
              </>
            )}
            {kind !== "all" &&
              (kind === "runners"
                ? " · сверху те, кто чаще бегал; волонтёрства не закрашены"
                : " · сверху те, кто чаще волонтёрил; пробежки не закрашены")}
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
