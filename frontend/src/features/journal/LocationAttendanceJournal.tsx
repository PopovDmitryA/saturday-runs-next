import { useEffect, useMemo, useState, type ReactNode } from "react";
import { formatDate, formatInt, pluralFormRu, pluralizeRu } from "../../lib/format";
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

// Кого считают строки: с 21.10.2026 в таблице только опознанные люди с
// ненулевым счётом в срезе, и номер последней строки — число разных людей.
// Подпись «из M …» называет их тем же словом, что итоговая строка.
// Родительный после «из»: «из 21 бегуна», «из 22 бегунов».
const KIND_PEOPLE_FORMS: Record<LocationAttendanceKind, readonly [string, string, string]> = {
  all: ["участника", "участников", "участников"],
  runners: ["бегуна", "бегунов", "бегунов"],
  volunteers: ["волонтёра", "волонтёров", "волонтёров"],
};

const TOTALS_LABELS: Record<LocationAttendanceKind, string> = {
  all: "Участников",
  runners: "Бегунов",
  volunteers: "Волонтёров",
};

// Таблица пуста, а старты были: во всём году нет никого, кого можно назвать
// строкой — «стартов не было» здесь было бы неправдой.
const EMPTY_SLICE_NOTES: Record<LocationAttendanceKind, string> = {
  all: "В протоколах этого года нет опознанных участников.",
  runners: "В протоколах этого года нет опознанных бегунов.",
  volunteers: "В протоколах этого года волонтёров нет.",
};

// Волонтёров parkrun по стартам сайт не собирает: из профиля parkrun
// приходит лишь сводка ролей без дат. У локации, где весь год стартовал
// только parkrun, пустая вкладка «Волонтёры» значит «не собираем», а не
// «волонтёров не было» (ревью 21.10.2026). Где есть и другая система, её
// волонтёры в журнале есть — там пустой срез честно говорит «нет».
const PARKRUN_NO_VOLUNTEERS_NOTE = "Волонтёров parkrun сайт не собирает.";

function onlyParkrunStarts(columns: LocationAttendance["columns"]): boolean {
  return (
    columns.length > 0 &&
    columns.every(
      (column) =>
        column.platforms.length > 0 && column.platforms.every((code) => code === "parkrun"),
    )
  );
}

// Неопознанных в таблице нет — у каждой безымянной строки протокола своя
// одноразовая личность, и строками они раздували счёт людей. Но итоговая
// строка их считает (это число людей на старте), и без этой фразы она
// расходилась бы с таблицей молча. Слово — как в подсказке к месту на
// странице локации («неопознанные финишёры»), без «НЕИЗВЕСТНЫЙ» в скобках: в
// протоколе это и строки с именем, но без штрихкода, и «Неизвестный бегун»
// RunPark, и «Unknown #…».
function unidentifiedNote(
  runs: number,
  vols: number,
  totalsLabel: string,
  inMonth: boolean,
): string | null {
  const parts: string[] = [];
  if (runs > 0) {
    parts.push(pluralizeRu(runs, ["неопознанный финиш", "неопознанных финиша", "неопознанных финишей"]));
  }
  if (vols > 0) {
    parts.push(
      pluralizeRu(vols, [
        "неопознанное волонтёрство",
        "неопознанных волонтёрства",
        "неопознанных волонтёрств",
      ]),
    );
  }
  if (parts.length === 0) {
    return null;
  }
  const one = parts.length === 1 && pluralFormRu(runs + vols, ["one", "many", "many"]) === "one";
  const counted = one ? (runs > 0 ? "учтён" : "учтено") : "учтены";
  // С выбранным месяцем число — за месяц, как итоговая строка под ним, а «из
  // M» строкой выше — люди года: без «в этом месяце» они читались бы как
  // числа за один период.
  const period = inMonth ? " в этом месяце" : "";
  return `Ещё ${parts.join(" и ")}${period} в таблицу не ${one ? "входит" : "входят"}, но ${counted} в строке «${totalsLabel}»`;
}

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
    monthTotals: row.month_totals,
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
  // конкретную субботу глазами тяжело. И он же уходит на сервер: журнал
  // приходит порциями по 50, и без месяца порция режется по годовому счёту —
  // новички месяца оставались за «Показать ещё», и фильтр их будто не видел
  // (Мещерский, сентябрь 2026: видно 25 волонтёров из 45).
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
    getLocationAttendance(slug, { year, kind, month: month === "all" ? null : month })
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
  }, [slug, year, kind, month]);

  // Сменилась локация — прежний месяц к ней отношения не имеет. Смену года
  // сбрасывает сам переключатель года: эффект здесь дал бы лишний запрос со
  // старым месяцем.
  useEffect(() => {
    setMonth("all");
  }, [slug]);

  const loadMore = () => {
    if (!data) {
      return;
    }
    setLoadingMore(true);
    getLocationAttendance(slug, {
      year: data.year,
      kind: data.kind,
      month: data.month ?? null,
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
    // Себя узнаём по handle — сервер отдаёт его в «me» тем же правилом, что
    // в строках. Сравнение точное: по хвосту id хендл «12» совпал бы и с
    // чужим «ivan-12».
    if (data.me && data.me.year_total > 0) {
      const meHandle = data.me.handle;
      const index = meHandle ? listed.findIndex((row) => row.handle === meHandle) : -1;
      const existing = index >= 0 ? result[index] : undefined;
      if (existing) {
        existing.me = true;
        existing.name = matrixRow(data.me, -1, true).name;
      } else {
        result.unshift(matrixRow(data.me, -1, true));
      }
    }
    return result;
  }, [data, extraRows]);

  // Счёт следует за выбранным месяцем: с фильтром «Всего» показывает участия
  // этого месяца, а не всего года — иначе колонка спорит с клетками рядом
  // (Дмитрий 03.09.2026). Число берём с сервера (month_totals): у закрытого
  // профиля клеток нет вовсе, и по ним посчитать было бы нечего, а цифра
  // нужна во всех строках. Счёт по клеткам — запасной путь для старого
  // payload из кэша.
  const monthRows = useMemo(() => {
    if (month === "all") {
      return rows;
    }
    return rows.map((row) => ({
      ...row,
      total:
        row.monthTotals?.[month] ??
        Object.keys(row.cells).filter((date) => date.slice(0, 7) === month).length,
    }));
  }, [rows, month]);

  // Поиск и сортировка идут по уже загруженным строкам: сервер отдаёт журнал
  // порциями, и «Показать ещё» подтягивает следующие. Об этом честно написано
  // под таблицей — «Показаны N из M».
  const visibleRows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = needle
      ? monthRows.filter((row) => (row.searchName ?? "").toLowerCase().includes(needle))
      : monthRows;
    if (!sort) {
      return filtered;
    }
    const sorted = [...filtered].sort((a, b) => {
      if (sort.key === "name") {
        return (a.searchName ?? "").localeCompare(b.searchName ?? "", "ru");
      }
      return (a.total ?? 0) - (b.total ?? 0);
    });
    if (!sort.asc) {
      sorted.reverse();
    }
    return sorted;
  }, [monthRows, query, sort]);

  const toggleSort = (key: MatrixSortKey) => {
    setSort((current) => {
      if (current?.key !== key) {
        // Имя — от А, счёт — от большего: так их и читают.
        return { key, asc: key === "name" };
      }
      return current.asc ? { key, asc: false } : null;
    });
  };

  // Всё, что описывает загруженные строки, берёт срез из ответа, а не из
  // нажатой вкладки: total_rows с 21.10.2026 у каждого среза свой, и пока
  // грузится новая вкладка, под прежней таблицей стояло бы «из 216
  // волонтёров». Вкладка и легенда откликаются на нажатие сразу.
  const view = data?.kind ?? kind;

  // Итоговая строка следует срезу: в «Бегунах» считает бегунов, в
  // «Волонтёрах» — волонтёров, в «Все» — и тех, и других.
  const totals = useMemo(() => {
    if (!data) {
      return undefined;
    }
    const values: Record<string, number> = {};
    for (const [date, counters] of Object.entries(data.date_totals)) {
      values[date] =
        view === "runners"
          ? counters.runners
          : view === "volunteers"
            ? counters.volunteers
            : // Люди без повторов: кто бежал и волонтёрил в один день — один
              // человек. Сумма — фолбэк для кэшированных ответов без поля.
              counters.people || counters.runners + counters.volunteers;
    }
    const label = TOTALS_LABELS[view];
    return {
      label,
      values,
      title: `${label} на этом старте — по всем за год, включая неопознанных, а не только по строкам таблицы`,
    };
  }, [data, view]);

  // Неопознанные за видимые даты: с выбранным месяцем — за месяц, как и
  // итоговая строка под ними.
  const unidentifiedText = useMemo(() => {
    if (!data) {
      return null;
    }
    let runs = 0;
    let vols = 0;
    for (const column of columns) {
      const counters = data.date_totals[column.key];
      runs += counters?.unidentified_runners ?? 0;
      vols += counters?.unidentified_volunteers ?? 0;
    }
    return unidentifiedNote(
      view === "volunteers" ? 0 : runs,
      view === "runners" ? 0 : vols,
      TOTALS_LABELS[view],
      month !== "all",
    );
  }, [data, view, columns, month]);

  const period = month === "all" ? "году" : "месяце";
  const peopleWord = data ? pluralFormRu(data.total_rows, KIND_PEOPLE_FORMS[view]) : "";
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
              onChange={(value) => {
                setYear(value);
                setMonth("all");
              }}
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
              view === "runners" ? "Пробежек" : view === "volunteers" ? "Волонтёрств" : "Всего"
            }
            // Подпись обязана совпадать с тем, что в колонке: с выбранным
            // месяцем там уже не год.
            totalHint={
              view === "all"
                ? `Дней активности в выбранном ${period}: день с пробежкой и волонтёрством считается одним днём`
                : view === "runners"
                  ? `Пробежек на этой площадке в выбранном ${period}`
                  : `Волонтёрств на этой площадке в выбранном ${period}`
            }
            totals={totals}
            // Пустая таблица — не всегда «стартов не было»: срез без людей
            // (волонтёры parkrun) или поиск, не нашедший никого.
            emptyNote={
              columns.length === 0
                ? "За этот год стартов не было."
                : data.total_rows === 0
                  ? view === "volunteers" && onlyParkrunStarts(data.columns)
                    ? PARKRUN_NO_VOLUNTEERS_NOTE
                    : EMPTY_SLICE_NOTES[view]
                  : "Никого не нашлось."
            }
          />
          {/* «Показаны 0 из 0» под пустой таблицей ничего не добавляет. */}
          {data.total_rows > 0 && (
            <p className="aj-meta muted">
              {query.trim() ? (
                <>
                  Найдено {formatInt(visibleRows.length)} среди загруженных{" "}
                  {formatInt(Math.min(shownRows, data.total_rows))} из{" "}
                  {formatInt(data.total_rows)} {peopleWord}
                </>
              ) : (
                <>
                  Показаны {formatInt(Math.min(shownRows, data.total_rows))} из{" "}
                  {formatInt(data.total_rows)} {peopleWord}
                </>
              )}
              {view !== "all" &&
                (view === "runners"
                  ? ` · сверху те, кто чаще бегал${month === "all" ? "" : " в этом месяце"}; волонтёрства не закрашены`
                  : ` · сверху те, кто чаще волонтёрил${month === "all" ? "" : " в этом месяце"}; пробежки не закрашены`)}
            </p>
          )}
          {/* Отдельной строкой: в одном абзаце с «Показаны N из M» на
              телефоне число людей тонуло в пяти строках текста. */}
          {unidentifiedText && <p className="aj-meta muted">{unidentifiedText}</p>}
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
