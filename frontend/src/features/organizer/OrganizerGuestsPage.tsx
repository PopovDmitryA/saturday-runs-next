import { useCallback, useId, useMemo, type Ref } from "react";
import { RequireAuth } from "../../components/RequireAuth";
import { TableViewToggle } from "../../components/tableUx/TableViewToggle";
import { TableWrap } from "../../components/tableUx/TableWrap";
import type { AdaptiveColumn } from "../../components/tableUx/useAdaptiveColumns";
import { useTableColumns } from "../../components/tableUx/useTableColumns";
import { useCachedResource } from "../../hooks/useCachedResource";
import { useRestorableState } from "../../hooks/useRestorableState";
import { formatInt, pluralizeRu } from "../../lib/format";
import { locationHintFor } from "../../lib/locationHint";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { PlanDateTabs } from "../planner/PlanDateTabs";
import { planDayLabel } from "../planner/PlanModal";
import { PlanAudienceNote, PlanMarkTag, PlansHint } from "../planner/PlanPeople";
import {
  getOrganizerGuests,
  type GuestGain,
  type GuestRow,
  type OrganizerGuestsDate,
  type PlanMark,
} from "../planner/plannerApi";
import { SERIES_ICON } from "../planner/seriesText";
import {
  GUESTS_TOOL_LABEL,
  GUESTS_WEEKS,
  GuestHomePlace,
  organizerGuestHref,
  organizerGuestsCacheKey,
  plannersLine,
} from "./guestParts";
import { OrganizerBreadcrumbs } from "./OrganizerBreadcrumbs";
import { OrganizerDenied } from "./OrganizerDenied";
import "../../components/filters/filters.css";
import "../planner/planner.css";
import "./organizer.css";
import "./organizerGuests.css";

/**
 * Столбцы «Кто у нас бежит» в порядке важности — для краткого вида
 * (useTableColumns): на телефоне — сколько влезает, остальное по «Полно».
 * Ширины — те же, что min-width столбцов в organizerGuests.css. Порядок в
 * таблице свой: Участник | Пробежек | У нас | Знаковый старт | Челленджи | Откуда.
 */
const GUEST_COLUMNS: AdaptiveColumn[] = [
  // Длинная фамилия — целиком, не посреди слова (ревью 26.10.2026, UI-2).
  { key: "name", width: 130, required: true },
  { key: "runs", width: 98, required: true },
  // Метка в узком столбце переносится; одной строкой (шире 36rem) — до 125.
  // Не влез столбец — метки под именем (GuestTableRow).
  { key: "marks", width: 103 },
  // «новая локация (31-я)» — в две строки по 120.
  { key: "gains", width: 120 },
  { key: "here", width: 66 },
  { key: "home", width: 158 },
];

const GAINS_HINT =
  "Что этот старт даст человеку в челленджах сайта: новая локация, регион, буква «Алфавита», рост в " +
  "номерных челленджах — «Нумератор», Фибоначчи, «Клуб Нельсона» и других — и медаль, если старт её принесёт. " +
  "Считаем на три ближайших старта и как будто его более ранние публичные планы уже сбылись.";

const REGULAR_HINT = "Бегает здесь каждую субботу";

/** Строка таблицы: разовый или постоянный (значок ↻ у имени). */
type GuestTableRowData = GuestRow & { regular?: boolean };

const MARKS_HINT =
  "Юбилеи считаем только на три ближайших старта. Номер — по планам человека: старты до этого, " +
  "которые он не отметил, не прибавляем.";

type SortKey = "runs" | "here";

type GuestSort = { key: SortKey; direction: "asc" | "desc" };

const DEFAULT_SORT: GuestSort = { key: "runs", direction: "desc" };

function sortValue(row: GuestTableRowData, key: SortKey): number | null {
  return key === "runs" ? row.runs : row.runs_here;
}

/**
 * Числа — в выбранную сторону, «не посчитать» (профиль не привязан) — всегда
 * в конце: наверху по возрастанию они читались бы как «ноль пробежек». При
 * равных — порядок сервера (у кого больше пробежек, кто раньше отметил).
 */
function sortRows(rows: GuestTableRowData[], sort: GuestSort): GuestTableRowData[] {
  const factor = sort.direction === "asc" ? 1 : -1;
  return rows
    .map((row, index) => ({ row, index }))
    .sort((left, right) => {
      const a = sortValue(left.row, sort.key);
      const b = sortValue(right.row, sort.key);
      if (a == null || b == null) {
        return a == null && b == null ? left.index - right.index : a == null ? 1 : -1;
      }
      return a !== b ? (a - b) * factor : left.index - right.index;
    })
    .map((item) => item.row);
}

function SortHeader({
  label,
  sortKey,
  sort,
  onSort,
}: {
  label: string;
  sortKey: SortKey;
  sort: GuestSort;
  onSort: (key: SortKey) => void;
}) {
  const active = sort.key === sortKey;
  return (
    <th
      scope="col"
      className="org-guests-num"
      aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}
    >
      {/* Тап по заголовку сортирует — подсказку тач-режима здесь не показываем. */}
      <button
        type="button"
        className={`th-col-label org-guests-sort${active ? " th-col-label-active" : ""}`}
        title="Сортировать по этому столбцу"
        data-tap-tooltip="off"
        onClick={() => onSort(sortKey)}
      >
        {label}
        <span className="th-col-sort" aria-hidden="true">
          {active ? (sort.direction === "asc" ? "↑" : "↓") : "↕"}
        </span>
      </button>
    </th>
  );
}

/**
 * «(31-я)» не рвётся ни по дефису, ни перед скобкой: в узком столбце
 * переносилось «новая локация (33- / я)». «21 → 22» — тоже одним куском.
 */
function gainText(label: string): string {
  return label.replace(/ \((\d+)-/g, "\u00a0($1\u2011").replace(/(\d) → (\d)/g, "$1\u00a0→\u00a0$2");
}

/**
 * «новая локация (31-я)», «буква «К»», «Нумератор ПРО: 21 → 22», «🥈 Клуб
 * Нельсона» — по строке на находку, полный текст — в подсказке. Номерные
 * челленджи визита — одной строкой, их подсказка — списком.
 */
function GuestGains({ gains }: { gains: GuestGain[] }) {
  return (
    <ul className="org-guests-gain-list">
      {gains.map((gain) => (
        <li key={gain.label} title={gain.hint}>
          {gainText(gain.label)}
        </li>
      ))}
    </ul>
  );
}

function GuestMarks({ marks, className }: { marks: PlanMark[]; className: string }) {
  return (
    <span className={className}>
      {marks.map((mark, index) => (
        <PlanMarkTag key={`${mark.kind}-${index}`} mark={mark} />
      ))}
    </span>
  );
}

/**
 * Строка человека. «У нас» — «—», если человек приедет впервые: это уже
 * сказано меткой «впервые у нас» в «Знаковом старте». Профиль не привязан —
 * так и пишем на месте чисел: под непривязанным штрихкодом у него могут быть
 * десятки пробежек у вас, и «0» было бы неправдой.
 *
 * Столбец «Знаковый старт» не влез (телефон, краткий вид) — метки строкой
 * под именем: юбилеи — то, ради чего организатор сюда и пришёл, и прятать
 * их за «Полно» с прокруткой вбок нельзя (ревью 26.10.2026, UI-5).
 */
function GuestTableRow({
  row,
  slug,
  show,
}: {
  row: GuestTableRowData;
  slug: string;
  show: (key: string) => boolean;
}) {
  const gains = row.gains ?? [];
  return (
    <tr>
      <td className="org-guests-name">
        {row.handle ? (
          <a className="loc-runner-link" href={organizerGuestHref(slug, row.handle)}>
            {row.name}
          </a>
        ) : (
          row.name
        )}
        {/* Неразрывный пробел: в узком столбце «↻» не уходит один на строку. */}
        {row.regular && (
          <>
            {"\u00a0"}
            <span className="plan-people-regular" title={REGULAR_HINT}>
              <span aria-hidden="true">{SERIES_ICON}</span>
              <span className="visually-hidden">, {REGULAR_HINT.toLowerCase()}</span>
            </span>
          </>
        )}
        {!show("marks") && row.marks.length > 0 && (
          <GuestMarks marks={row.marks} className="org-guests-mark-list org-guests-name-marks" />
        )}
      </td>
      {row.runs == null ? (
        <td className="org-guests-unlinked" colSpan={show("here") ? 2 : 1}>
          профиль не привязан
        </td>
      ) : (
        <>
          <td className="org-guests-num">{formatInt(row.runs)}</td>
          {show("here") && (
            <td className="org-guests-num">
              {row.first_here || row.runs_here == null ? (
                <span className="org-guests-none-value">—</span>
              ) : (
                formatInt(row.runs_here)
              )}
            </td>
          )}
        </>
      )}
      {show("marks") && (
        <td className="org-guests-marks">
          {row.marks.length > 0 && <GuestMarks marks={row.marks} className="org-guests-mark-list" />}
        </td>
      )}
      {show("gains") && <td className="org-guests-gains">{gains.length > 0 && <GuestGains gains={gains} />}</td>}
      {show("home") && <td className="org-guests-home">{row.home && <GuestHomePlace home={row.home} />}</td>}
    </tr>
  );
}

/** Таблица людей даты: разовые и постоянные вместе, сортировка и «Кратко | Полно». */
function GuestTable({
  rows,
  slug,
  show,
  showFull,
  minWidth,
  measureRef,
  sort,
  onSort,
}: {
  rows: GuestTableRowData[];
  slug: string;
  show: (key: string) => boolean;
  showFull: boolean;
  minWidth: string;
  measureRef?: Ref<HTMLDivElement>;
  sort: GuestSort;
  onSort: (key: SortKey) => void;
}) {
  return (
    <TableWrap className="org-guests-wrap" stickyFirstCol={showFull} outerRef={measureRef}>
      <table className="data-table org-guests-table" style={{ minWidth }}>
        {/* Ширины столбцов задаёт colgroup, а не содержимое: при смене
            вкладки даты столбцы не прыгают. Остаток ширины — «Откуда», без
            него — «Участник». */}
        <colgroup>
          <col className={show("home") ? "org-guests-col-name org-guests-col-name-fixed" : "org-guests-col-name"} />
          <col className="org-guests-col-runs" />
          {show("here") && <col className="org-guests-col-here" />}
          {show("marks") && <col className="org-guests-col-marks" />}
          {show("gains") && <col className="org-guests-col-gains" />}
          {show("home") && <col className="org-guests-col-home" />}
        </colgroup>
        <thead>
          <tr>
            <th scope="col">Участник</th>
            <SortHeader label="Пробежек" sortKey="runs" sort={sort} onSort={onSort} />
            {show("here") && <SortHeader label="У нас" sortKey="here" sort={sort} onSort={onSort} />}
            {show("marks") && (
              <th scope="col">
                Знаковый{" "}
                <span className="org-guests-th-hint">
                  старт
                  <PlansHint text={MARKS_HINT} label="Как считаем знаковые старты" />
                </span>
              </th>
            )}
            {show("gains") && (
              <th scope="col">
                <span className="org-guests-th-hint">
                  Челленджи
                  <PlansHint text={GAINS_HINT} label="Как считаем челленджи" />
                </span>
              </th>
            )}
            {show("home") && <th scope="col">Откуда</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <GuestTableRow key={`${row.handle ?? row.name}-${index}`} row={row} slug={slug} show={show} />
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

/**
 * Постоянные на дату — строки общего состава (regulars_people, один на
 * ответ) в их порядке, с метками знаковых стартов и челленджами этой даты.
 */
function regularRows(date: OrganizerGuestsDate, people: GuestRow[]): GuestTableRowData[] {
  const regulars = date.regulars;
  if (!regulars || regulars.handles.length === 0) {
    return [];
  }
  const byHandle = new Map(people.filter((row) => row.handle).map((row) => [row.handle as string, row]));
  return regulars.handles.flatMap((handle) => {
    const row = byHandle.get(handle);
    return row
      ? [{ ...row, marks: regulars.marks[handle] ?? [], gains: regulars.gains?.[handle] ?? [], regular: true }]
      : [];
  });
}

function DatePanel({
  date,
  slug,
  regularsPeople,
}: {
  date: OrganizerGuestsDate;
  slug: string;
  regularsPeople: GuestRow[];
}) {
  const tableColumns = useTableColumns(GUEST_COLUMNS);
  const { show, showFull } = tableColumns;
  // Сортировка переживает «Назад» со страницы участника, как и выбранная дата.
  const [chosenSort, setSort] = useRestorableState<GuestSort>("organizer.guests.sort", DEFAULT_SORT);
  // Столбца, по которому выбрали сортировку, сейчас нет на экране («У нас»
  // выбрали в «Полно» и вернулись в «Кратко») — порядок по умолчанию: строки
  // по невидимому столбцу ничем не объяснены (ревью 26.10.2026, UI-6). Выбор
  // помним — в «Полно» он вернётся.
  const sort = show(chosenSort.key) ? chosenSort : DEFAULT_SORT;
  // Разовые и постоянные — одной таблицей (решение Дмитрия 27.10.2026, как на
  // странице локации): у постоянного — ↻ у имени. При равном числе пробежек
  // разовые выше: sortRows устойчив.
  const rows = useMemo(
    () => sortRows([...date.people, ...regularRows(date, regularsPeople)], sort),
    [date, regularsPeople, sort],
  );
  const regulars = date.regulars ?? null;
  const privateCount = date.private + (regulars?.private ?? 0);
  const childrenCount = date.children + (regulars?.children ?? 0);
  // Повторный клик по тому же столбцу переворачивает порядок, по новому —
  // сначала больше: у чисел интересен максимум. «Тот же» — тот, что виден
  // отсортированным сейчас.
  const toggleSort = useCallback(
    (key: SortKey) =>
      setSort(
        sort.key === key
          ? { key, direction: sort.direction === "asc" ? "desc" : "asc" }
          : { key, direction: "desc" },
      ),
    [setSort, sort],
  );

  return (
    <>
      <div className="org-guests-day-row">
        {/* Дата уже в заголовке страницы — здесь название особого старта и кого ждём. */}
        <p className="org-guests-day">
          {date.kind === "special" && <strong>{date.title || "особый старт"} · </strong>}
          <span className="muted">{plannersLine(date.total, regulars?.total ?? 0)}</span>
        </p>
        {rows.length > 0 && <TableViewToggle columns={tableColumns} inline />}
      </div>
      {rows.length > 0 ? (
        <GuestTable
          rows={rows}
          slug={slug}
          show={show}
          showFull={showFull}
          minWidth={tableColumns.minWidth}
          measureRef={tableColumns.measureRef}
          sort={sort}
          onSort={toggleSort}
        />
      ) : (
        date.total > 0 && (
          <p className="muted org-guests-none">
            По имени — никого: у всех, кто собирается, планы приватные или это дети.
          </p>
        )
      )}
      <PlanAudienceNote named={rows.length} children={childrenCount} private={privateCount} />
    </>
  );
}

/** «сб 31 октября» в заголовке — целиком: на телефоне перенос после «·», а не внутри даты. */
function guestsDateLabel(iso: string): string {
  return planDayLabel(iso).replace(/ /g, "\u00a0");
}

/**
 * «Кто у нас бежит»: люди с публичными планами на ближайшие старты локации —
 * таблицей с названиями столбцов: сколько у человека пробежек, сколько из
 * них у вас, какой знаковый старт его ждёт и откуда он едет (решения Дмитрия
 * 25.10.2026: на странице локации — только имя и число, юбилеи и «впервые у
 * нас» — здесь, отдельным столбцом, а не на странице участника). Приватные и
 * дети — числом, как везде. Даты — вкладками, как в блоке «Планируют
 * пробежать» на странице локации: двенадцать недель списком растянули бы
 * страницу.
 *
 * Заголовок — «Кто у нас бежит · сб 31 октября»: дата выбранной вкладки,
 * при переключении меняется вместе с таблицей (решение Дмитрия 26.10.2026 —
 * раздел назывался «Кто к нам едет»). Название локации — в крошках и колонке.
 */
function OrganizerGuestsContent({ slug }: { slug: string }) {
  const key = organizerGuestsCacheKey(slug);
  const { data, loading, error, forbidden, notFound } = useCachedResource(
    key,
    () => getOrganizerGuests(slug, GUESTS_WEEKS),
    [key],
    { errorText: "Не удалось загрузить, кто у вас бежит" },
  );
  // Выбранная дата переживает «Назад» со страницы участника; null — ближайшая.
  const [picked, setPicked] = useRestorableState<string | null>("organizer.guests.date", null);
  const baseId = useId();

  const name = data?.location.name ?? locationHintFor(slug)?.name ?? null;
  const sidebar = {
    active: "organizer" as const,
    location: name ? { slug, name } : locationHintFor(slug),
  };

  if (forbidden || notFound) {
    return (
      <PortalSectionShell sidebar={sidebar}>
        <OrganizerDenied slug={slug} notFound={notFound} />
      </PortalSectionShell>
    );
  }

  const dates = data?.dates ?? [];
  // Выбранной даты не стало (план убрали, старт прошёл) — снова ближайшая.
  const current = dates.find((item) => item.date === picked) ?? dates[0];
  const tabId = (date: string) => `${baseId}-tab-${date}`;
  const panelId = `${baseId}-panel`;

  return (
    <PortalSectionShell sidebar={sidebar}>
      <header className="loc-header">
        <OrganizerBreadcrumbs slug={slug} locationName={name} tool={GUESTS_TOOL_LABEL} />
        <div className="loc-header-title">
          <h1>
            {GUESTS_TOOL_LABEL}
            {current && ` · ${guestsDateLabel(current.date)}`}
          </h1>
        </div>
        <p className="muted">
          Кто отметил «Планирую» на ближайшие старты: сколько у человека пробежек, сколько из них у вас, какой
          знаковый старт его ждёт, что старт даст ему в челленджах и откуда он едет. Постоянные — те, кто
          отметил «каждую субботу», — со значком {SERIES_ICON}. По имени — только те, чей план публичный;
          остальные и дети — числом. Нажмите на имя — откроется страница участника.
        </p>
      </header>

      {error && !data && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {loading && !data && <p className="muted">Загрузка…</p>}

      {data && !current && (
        <div className="card">
          <p className="muted">
            На ближайшие {pluralizeRu(data.weeks || GUESTS_WEEKS, ["неделя", "недели", "недель"])} к вам пока
            никто не собирается. Участники отмечают дату кнопкой «Планирую» на странице локации — здесь они
            появятся сразу.
          </p>
        </div>
      )}

      {data && current && (
        // ym-hide-content — таблицу с цифрами и домами людей вебвизор Метрики не
        // записывает: по общему согласию (пункт о планах) их видят только организаторы.
        <section className="card org-guests-card ym-hide-content" aria-label="Кто планирует пробежать">
          <PlanDateTabs dates={dates} current={current} tabId={tabId} panelId={() => panelId} onSelect={setPicked} />
          <div className="org-guests-panel" role="tabpanel" id={panelId} aria-labelledby={tabId(current.date)}>
            <DatePanel date={current} slug={slug} regularsPeople={data.regulars_people ?? []} />
          </div>
        </section>
      )}
    </PortalSectionShell>
  );
}

export function OrganizerGuestsPage({ slug }: { slug: string }) {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {() => <OrganizerGuestsContent slug={slug} />}
    </RequireAuth>
  );
}
