import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
} from "react";
import { useCachedResource } from "../../hooks/useCachedResource";
import { useRestorableState } from "../../hooks/useRestorableState";
import { readCached, writeCached } from "../../lib/dataCache";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { LocationStatusBadge } from "../../components/LocationStatusBadge";
import { PlatformBadge } from "../../components/PlatformBadge";
import { WeatherChip } from "../../components/WeatherChip";
import { PlatformFilter } from "../../components/filters/PlatformFilter";
import { ScrollToTopButton } from "../../components/ScrollToTopButton";
import {
  FilterGroup,
  FilterPanel,
  FilterRow,
  FilterSearch,
  FilterSelect,
  FilterTabs,
} from "../../components/filters/FilterPanel";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import {
  getLastResults,
  getLastResultsWeek,
  getLastResultsWeeks,
  type LastResultsItem,
  type LastResultsResponse,
  type LastResultsWeek,
  type LastResultsWeekResponse,
  type LastResultsWeeks,
} from "../../lib/api";
import {
  COUNT_FORMS,
  formatDate,
  formatFinishTimeValue,
  formatInt,
  formatWeekdayShort,
  parseIsoDate,
  platformCodeLabel,
  pluralizeRu,
} from "../../lib/format";
import {
  locationProtocolHref,
  saturdayOfWeek,
  unifiedProtocolWeekHref,
} from "../../lib/protocolHref";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { TableViewToggle } from "../../components/tableUx/TableViewToggle";
import { useTableColumns, type TableColumns } from "../../components/tableUx/useTableColumns";
import type { AdaptiveColumn } from "../../components/tableUx/useAdaptiveColumns";

/**
 * Два вида одной таблицы (решение Дмитрия 21.10.2026):
 * - «Последний старт» — строка на локацию, её последний старт (как было);
 * - «За неделю» — все старты одной недели пн–вс, подписанной субботой, как в
 *   едином протоколе: логика недель на сайте одна. Строка — старт, а не
 *   локация: воскресенье и праздник в будни стоят своими строками рядом с
 *   субботой (18.10.2026 стартовал один «Кулибин»).
 * До второй правки того же дня второй вид был «За день» (?date=, релиз 3.9.0).
 */
type ResultsMode = "last" | "week";

const MODE_TABS = [
  { value: "last", label: "Последний старт" },
  { value: "week", label: "За неделю" },
] as const;

// parkrun в России нет с 2022 года: в «Последнем старте» это девять локаций на
// паузе (видны с «неактивными»), а в истории недель он есть с 2014-го. Кнопка
// стоит всегда и гаснет, когда его строк на экране нет: появляйся она по
// данным, ряд фильтров на части ширин переносился бы и сдвигал таблицу.
const PLATFORM_FILTERS: readonly string[] = ["five_verst", "s95", "runpark", "parkrun"];

const WEEK_PARAM = "week";
// Ссылки 3.9.0 на день (?date=) не ломаются: день ведёт в свою неделю.
const LEGACY_DATE_PARAM = "date";

/**
 * Проверенная дата YYYY-MM-DD или null. Несуществующую дату (2026-02-31)
 * регулярка пропускает, а Date молча перекатывает в март, поэтому сверяем
 * разобранное обратно с исходником.
 */
function validIsoDate(raw: string | null): string | null {
  if (!raw || !/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    return null;
  }
  const parsed = parseIsoDate(raw);
  if (
    !parsed ||
    parsed.getFullYear() !== Number(raw.slice(0, 4)) ||
    parsed.getMonth() + 1 !== Number(raw.slice(5, 7)) ||
    parsed.getDate() !== Number(raw.slice(8, 10))
  ) {
    return null;
  }
  return raw;
}

/**
 * Суббота недели из адреса или null — тогда открывается «Последний старт».
 * ?week= с любым днём недели и старый ?date= приводятся к субботе: адрес
 * у недели один, и эффект ниже перепишет его на ?week=<суббота>.
 */
function readWeekParam(): string | null {
  const params = new URLSearchParams(window.location.search);
  const day = validIsoDate(params.get(WEEK_PARAM)) ?? validIsoDate(params.get(LEGACY_DATE_PARAM));
  // Суббота тоже проверяется: из набранного руками адреса (последняя неделя
  // 9999 года) она уехала бы в 10000-й год, а такую дату сервер не примет, и
  // «Повторить» слал бы её снова и снова.
  return day ? validIsoDate(saturdayOfWeek(day)) : null;
}

/**
 * Неделя — в адрес, вид и фильтры — нет: replaceState правит текущую запись
 * истории, страница не перемонтируется, и выбранные системы и поиск остаются
 * на месте (тот же приём, что ?view= у журнала локации). Старый ?date= при
 * этом снимается всегда.
 */
function writeWeekParam(saturday: string | null): void {
  const url = new URL(window.location.href);
  url.searchParams.delete(LEGACY_DATE_PARAM);
  if (saturday) {
    url.searchParams.set(WEEK_PARAM, saturday);
  } else {
    url.searchParams.delete(WEEK_PARAM);
  }
  const next = `${url.pathname}${url.search}${url.hash}`;
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (next !== current) {
    window.history.replaceState(window.history.state, "", next);
  }
}

function resultsWeekHref(saturday: string): string {
  return `/results?${WEEK_PARAM}=${saturday}`;
}

/** «сб 17.10.2026» — дата старта в колонке «Дата» вида «За неделю». */
function dayLabel(day: string): string {
  return `${formatWeekdayShort(day)} ${formatDate(day)}`;
}

/** «16.10.2026» → «16.10» и «.2026» — год на узком экране прячется (CSS). */
function splitYear(day: string): [string, string] {
  const full = formatDate(day);
  return [full.slice(0, 5), full.slice(5)];
}

const LAST_RESULTS_KEY = "locations:last-results";
const WEEKS_KEY = "locations:last-results:weeks";

function weekKey(saturday: string): string {
  return `locations:last-results:week:${saturday}`;
}

// Запросы, которые уже в пути: предзагрузка по наведению и загрузка по клику
// делят один, а не шлют два подряд.
const inflight = new Map<string, Promise<unknown>>();

function sharedLoad<T>(key: string, load: () => Promise<T>): Promise<T> {
  let pending = inflight.get(key) as Promise<T> | undefined;
  if (!pending) {
    pending = load().finally(() => inflight.delete(key));
    inflight.set(key, pending);
  }
  return pending;
}

/**
 * Подгрузить ответ заранее — по наведению на панель или стрелку, а не при
 * каждом заходе: неделя весит ~30 КБ, а в «За неделю» уходит не каждый. Кэш
 * тот же, что у useCachedResource, поэтому клик рисует таблицу сразу.
 */
function prefetch<T>(key: string, load: () => Promise<T>): Promise<T | undefined> {
  const cached = readCached<T>(key);
  if (cached !== undefined) {
    return Promise.resolve(cached);
  }
  return sharedLoad(key, load).then(
    (payload) => {
      writeCached(key, payload);
      return payload;
    },
    () => undefined,
  );
}

function prefetchWeek(saturday: string): void {
  void prefetch(weekKey(saturday), () => getLastResultsWeek(saturday));
}

/** Список недель и неделя, на которой откроется «За неделю», — последняя. */
function prefetchWeekMode(): void {
  void prefetch(WEEKS_KEY, getLastResultsWeeks).then((weeks) => {
    const target = weeks?.latest_saturday ?? weeks?.weeks.at(-1)?.saturday;
    if (target) {
      prefetchWeek(target);
    }
  });
}

/**
 * Ссылка на неделю: обычный клик переключает вид на месте, а адрес в href
 * настоящий — с Ctrl/Cmd или средней кнопкой неделя открывается в новой
 * вкладке. Наведение и фокус заранее подгружают неделю.
 *
 * saturday = null — ссылка погашена (край истории), но остаётся тем же
 * элементом: подмена на <span> сбрасывала фокус клавиатуры в body, и читалка
 * экрана теряла место.
 */
function WeekLink({
  saturday,
  onSelect,
  title,
  ariaLabel,
  className,
  children,
}: {
  saturday: string | null;
  onSelect: (saturday: string) => void;
  title?: string;
  ariaLabel?: string;
  className?: string;
  children: ReactNode;
}) {
  if (saturday === null) {
    return (
      <a
        role="link"
        aria-disabled="true"
        tabIndex={0}
        title={title}
        aria-label={ariaLabel ?? title}
        className={className}
      >
        {children}
      </a>
    );
  }
  const onClick = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    event.preventDefault();
    onSelect(saturday);
  };
  const warm = () => prefetchWeek(saturday);
  return (
    <a
      href={resultsWeekHref(saturday)}
      title={title}
      aria-label={ariaLabel}
      className={className}
      onClick={onClick}
      onPointerEnter={warm}
      onFocus={warm}
      onTouchStart={warm}
    >
      {children}
    </a>
  );
}

/**
 * Выбор недели: стрелки к соседним неделям со стартами и список посередине.
 * Стоит в панели фильтров на месте «неактивных» и той же ширины
 * (.results-mode-slot): при смене вида ни панель, ни таблица под ней не
 * сдвигаются.
 *
 * Соседи ищутся и для недели вне списка (ссылка на неделю без стартов):
 * стрелки ведут к ближайшим неделям по обе стороны, а сама неделя встаёт в
 * список с нулём, чтобы кнопка не показывала сырое «2026-10-10». На краях
 * стрелка остаётся погашенной, а не пропадает — иначе список прыгал бы на её
 * ширину.
 */
function WeekNav({
  weeks,
  week,
  onSelect,
}: {
  weeks: LastResultsWeek[];
  week: string;
  onSelect: (saturday: string) => void;
}) {
  let previous: string | null = null;
  let next: string | null = null;
  for (const entry of weeks) {
    if (entry.saturday < week) {
      previous = entry.saturday;
    } else if (entry.saturday > week && next === null) {
      next = entry.saturday;
    }
  }
  const options = useMemo(() => {
    const list = weeks.some((entry) => entry.saturday === week)
      ? [...weeks]
      : [...weeks, { saturday: week, locations: 0, starts: 0 }];
    // Свежие сверху: чаще всего нужна одна из последних недель.
    list.sort((a, b) => (a.saturday < b.saturday ? 1 : -1));
    // Число локаций — отдельным куском: на телефоне кнопке между стрелками
    // тесно, и в ней число прячется (оно же стоит в строке над таблицей), а в
    // раскрытом списке остаётся у каждой недели.
    return list.map((entry) => ({
      value: entry.saturday,
      label: (
        <>
          {formatDate(entry.saturday)}
          <span className="results-week-count"> ({formatInt(entry.locations)})</span>
        </>
      ),
    }));
  }, [weeks, week]);

  return (
    <nav className="results-week-nav" aria-label="Выбор недели">
      <WeekLink
        saturday={previous}
        onSelect={onSelect}
        className={`results-week-arrow${previous ? "" : " results-week-arrow-off"}`}
        title={previous ? `Предыдущая неделя: ${formatDate(previous)}` : "Раньше стартов нет"}
        ariaLabel={previous ? `Предыдущая неделя: ${formatDate(previous)}` : "Раньше стартов нет"}
      >
        <span aria-hidden="true">←</span>
      </WeekLink>
      <FilterSelect
        ariaLabel="Неделя"
        value={week}
        onChange={(value) => onSelect(String(value))}
        options={options}
      />
      <WeekLink
        saturday={next}
        onSelect={onSelect}
        className={`results-week-arrow${next ? "" : " results-week-arrow-off"}`}
        title={next ? `Следующая неделя: ${formatDate(next)}` : "Это последняя неделя со стартами"}
        ariaLabel={next ? `Следующая неделя: ${formatDate(next)}` : "Это последняя неделя со стартами"}
      >
        <span aria-hidden="true">→</span>
      </WeekLink>
    </nav>
  );
}

type SortKey =
  | "weather"
  | "event_number"
  | "name"
  | "city"
  | "event_date"
  | "finishers"
  | "volunteers"
  | "debutants"
  | "first_here"
  | "guests"
  | "prs"
  | "best_male"
  | "best_female";

// Алфавит и времена логичнее открывать по возрастанию («лучше» = меньше).
const ASC_FIRST_KEYS: SortKey[] = ["name", "city", "best_male", "best_female"];
type SortState = { key: SortKey; asc: boolean };

/** Времена — как в журнале протоколов: без ведущих «00:» часов. */
function formatTime(display: string | null, sec: number | null): string {
  if (sec === null && !display) {
    return "—";
  }
  return formatFinishTimeValue(display, sec).replace(/^00:/, "");
}

function matchesQuery(item: LastResultsItem, query: string): boolean {
  const haystack = [item.name, item.city, item.region, item.country]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(query);
}

function sortValue(item: LastResultsItem, key: SortKey): number | string | null {
  switch (key) {
    case "event_number":
      return item.event_number;
    case "name":
      return item.name.toLowerCase();
    case "city":
      return (item.city ?? "").toLowerCase();
    case "event_date":
      return item.event_date;
    case "weather":
      return item.weather?.temperature_c ?? null;
    case "finishers":
      return item.finishers;
    case "volunteers":
      return item.volunteers;
    case "debutants":
      return item.debutants;
    case "first_here":
      return item.first_at_location;
    case "guests":
      return item.guests;
    case "prs":
      return item.prs;
    case "best_male":
      return item.best_male_time_sec;
    case "best_female":
      return item.best_female_time_sec;
  }
}

/**
 * Протокол старта строки. Полный протокол есть у нас — ведём на нашу страницу
 * протокола; иначе остаётся внешняя ссылка платформы, а нет и её — просто текст.
 */
function ProtocolLink({ item, children }: { item: LastResultsItem; children: ReactNode }) {
  if (item.has_protocol && item.event_platform_code) {
    return (
      <a
        href={locationProtocolHref(item.slug, item.event_platform_code, item.event_date)}
        title="Открыть протокол старта"
      >
        {children}
      </a>
    );
  }
  if (item.protocol_url) {
    return (
      <a href={item.protocol_url} target="_blank" rel="noreferrer" title="Открыть протокол">
        {children}
      </a>
    );
  }
  return <>{children}</>;
}

// Колонки краткого вида в порядке важности (ширины — как в CSS .loc-index-table).
const LAST_RESULTS_COLUMNS: AdaptiveColumn[] = [
  // Номер старта — первым столбцом (просьба Дмитрия 22.08.2026). Узкий и
  // обязательный: без него строка теряет главную примету «какой это по счёту».
  { key: "event_number", width: 72, required: true },
  { key: "name", width: 170, required: true },
  { key: "event_date", width: 160, required: true },
  { key: "finishers", width: 148, required: true },
  { key: "city", width: 160 },
  { key: "platform", width: 104 },
  { key: "weather", width: 96 },
  { key: "volunteers", width: 148 },
  { key: "debutants", width: 148 },
  { key: "first_here", width: 176 },
  { key: "guests", width: 148 },
  { key: "best_male", width: 184 },
  { key: "best_female", width: 184 },
  { key: "prs", width: 184 },
];

function LastResultsTable({
  mode,
  items,
  rowsKey,
  emptyText,
  tableColumns,
}: {
  mode: ResultsMode;
  items: LastResultsItem[];
  /** Чьи строки на экране — смена ключа мягко проявляет новые строки. */
  rowsKey: string;
  emptyText: string;
  tableColumns: TableColumns;
}) {
  const isWeek = mode === "week";
  // Своя сортировка у каждого вида: в «Последнем старте» по умолчанию свежие
  // сверху, в «За неделю» почти все даты — одна суббота, и сверху многолюдные.
  // Обе живут здесь же, а таблица при смене вида не перемонтируется: колонки у
  // видов одни и те же, и меняются только строки (правка Дмитрия 21.10.2026 —
  // смена вида не должна выглядеть как новая страница).
  const [lastSort, setLastSort] = useRestorableState<SortState>("results.sort", {
    key: "event_date",
    asc: false,
  });
  const [weekSort, setWeekSort] = useRestorableState<SortState>("results.week.sort", {
    key: "finishers",
    asc: false,
  });
  const sort = isWeek ? weekSort : lastSort;
  const setSort = isWeek ? setWeekSort : setLastSort;
  const showFull = tableColumns.showFull;
  const show = tableColumns.show;

  const sorted = useMemo(() => {
    const copy = [...items];
    copy.sort((a, b) => {
      const left = sortValue(a, sort.key);
      const right = sortValue(b, sort.key);
      if (left === right) {
        // При равенстве ключа — порядок бэкенда. Неделя пришла многолюдными
        // сверху, дальше по алфавиту и дате: сортировка стабильная, его и
        // оставляем — иначе у одной локации суббота и праздник менялись бы
        // местами.
        if (isWeek) {
          return 0;
        }
        // Последний старт — свежие и многолюдные выше.
        if (a.event_date !== b.event_date) {
          return a.event_date < b.event_date ? 1 : -1;
        }
        return (b.finishers ?? 0) - (a.finishers ?? 0);
      }
      if (left === null) {
        return 1;
      }
      if (right === null) {
        return -1;
      }
      const compare =
        typeof left === "string" && typeof right === "string"
          ? left.localeCompare(right, "ru")
          : left < right
            ? -1
            : 1;
      return sort.asc ? compare : -compare;
    });
    return copy;
  }, [items, sort, isWeek]);

  const toggleSort = (key: SortKey) => {
    setSort((current) =>
      current.key === key ? { key, asc: !current.asc } : { key, asc: ASC_FIRST_KEYS.includes(key) },
    );
  };

  const sortProps = (key: SortKey) => ({
    filterable: false,
    sortActive: sort.key === key,
    sortAsc: sort.asc,
    onSort: () => toggleSort(key),
  });

  const visibleCount = LAST_RESULTS_COLUMNS.filter((column) => show(column.key)).length;

  return (
    <>
      <TableWrap stickyFirstCol={showFull} outerRef={tableColumns.measureRef}>
        <table
          className={`data-table data-table-layout-fixed loc-index-table last-results-table${
            showFull ? "" : " data-table-short"
          }`}
          style={{ minWidth: tableColumns.minWidth }}
        >
          <colgroup>
            <col className="col-compact" />
            <col />
            {show("city") && <col className="col-city" />}
            {show("platform") && <col className="col-platform" />}
            <col className="col-date" />
            {show("weather") && <col className="col-weather" />}
            <col className="col-metric" />
            {show("volunteers") && <col className="col-metric" />}
            {show("debutants") && <col className="col-metric" />}
            {show("first_here") && <col className="col-metric-wide" />}
            {show("guests") && <col className="col-metric" />}
            {show("prs") && <col className="col-metric-wide" />}
            {show("best_male") && <col className="col-metric-wide" />}
            {show("best_female") && <col className="col-metric-wide" />}
          </colgroup>
          <thead>
            <tr>
              <ColumnHeader
                label="№"
                headerTitle="Номер старта у локации внутри её системы (сквозного номера по всем системам здесь нет)"
                {...sortProps("event_number")}
              />
              <ColumnHeader label="Локация" {...sortProps("name")} />
              {show("city") && <ColumnHeader label="Город" {...sortProps("city")} />}
              {show("platform") && <ColumnHeader label="Система" filterable={false} />}
              {/* Колонка одна у обоих видов (решение Дмитрия 21.10.2026): без
                  неё колонки правее съезжали бы на её ширину при каждой смене
                  вида. В «За неделю» она к тому же различает старты: суббота,
                  воскресенье, праздник в будни. */}
              <ColumnHeader
                label="Дата"
                hint={
                  isWeek
                    ? "Дата старта (клик по дате — протокол)"
                    : "Дата последнего старта локации (клик по дате — протокол)"
                }
                {...sortProps("event_date")}
              />
              {show("weather") && (
                <ColumnHeader
                  label="Погода"
                  headerTitle="Погода в час старта по архиву Open-Meteo. Клик по значению — подробности"
                  {...sortProps("weather")}
                />
              )}
              {/* Подсказки ниже — без «последнего»: те же колонки стоят и в
                  «За неделю», где старт не обязательно последний у локации. */}
              <ColumnHeader
                label="Финишёров"
                hint="Финишёров на старте"
                {...sortProps("finishers")}
              />
              {show("volunteers") && (
                <ColumnHeader
                  label="Волонтёров"
                  hint="Волонтёров на старте"
                  {...sortProps("volunteers")}
                />
              )}
              {show("debutants") && (
                <ColumnHeader
                  label="Новичков"
                  hint="Первый старт в своей системе"
                  {...sortProps("debutants")}
                />
              )}
              {/* Те же три расходящихся круга, что в журнале локации:
                  новички → впервые здесь → гости (правка Дмитрия 17.09.2026). */}
              {show("first_here") && (
                <ColumnHeader
                  label="Впервые здесь"
                  hint="Уже бегали в системе, но на эту локацию приехали впервые"
                  {...sortProps("first_here")}
                />
              )}
              {show("guests") && (
                <ColumnHeader
                  label="Гостей"
                  hint="Финишёры с другой домашней локацией, даже если бывают здесь не впервые. Обычно их больше, чем «Впервые здесь», но не всегда: дом считаем на сегодня, а у новичков его ещё нет. Прочерк — число ещё не пересчитано"
                  {...sortProps("guests")}
                />
              )}
              {show("prs") && (
                <ColumnHeader
                  label="Личных рекордов"
                  hint="Личных рекордов на этом старте"
                  {...sortProps("prs")}
                />
              )}
              {show("best_male") && (
                <ColumnHeader
                  label="Лучшее время (М)"
                  hint="Лучшее мужское время старта"
                  {...sortProps("best_male")}
                />
              )}
              {show("best_female") && (
                <ColumnHeader
                  label="Лучшее время (Ж)"
                  hint="Лучшее женское время старта"
                  {...sortProps("best_female")}
                />
              )}
            </tr>
          </thead>
          <tbody key={rowsKey} className="results-rows">
            {sorted.length === 0 ? (
              <tr>
                <td colSpan={visibleCount} className="table-empty-cell">
                  <span className="muted">{emptyText}</span>
                </td>
              </tr>
            ) : (
              // В «За неделю» у локации бывает два старта (суббота и праздник
              // в будни) — ключ строки по локации и дате.
              sorted.map((item) => (
                <tr key={`${item.identity_key}:${item.event_date}`}>
                  <td className="td-compact">
                    {item.event_number != null ? formatInt(item.event_number) : "—"}
                  </td>
                  <td className="td-location">
                    <a href={`/locations/${item.slug}`}>{item.name}</a>
                    <LocationStatusBadge isPaused={item.is_paused} isCancelled={item.is_cancelled} />
                  </td>
                  {show("city") && (
                    <td className="muted">
                      {item.city ?? "—"}
                      {item.country && item.country !== "Россия" ? ` · ${item.country}` : ""}
                    </td>
                  )}
                  {show("platform") && (
                    <td>
                      <span className="loc-index-platforms">
                        {item.event_platform_codes.map((code) => (
                          <PlatformBadge key={code} code={code} />
                        ))}
                      </span>
                    </td>
                  )}
                  {/* В «За неделю» — с днём недели: «вс 18.10.2026» сразу
                      видно рядом с субботами. */}
                  <td className={isWeek || item.is_last_saturday ? undefined : "muted"}>
                    <ProtocolLink item={item}>
                      {isWeek ? dayLabel(item.event_date) : formatDate(item.event_date)}
                    </ProtocolLink>
                  </td>
                  {show("weather") && (
                    <td className="td-compact td-weather">
                      <WeatherChip weather={item.weather} locationSlug={item.slug} locationName={item.name} />
                    </td>
                  )}
                  <td className="td-compact">{item.finishers != null ? formatInt(item.finishers) : "—"}</td>
                  {show("volunteers") && <td className="td-compact">{item.volunteers != null ? formatInt(item.volunteers) : "—"}</td>}
                  {show("debutants") && <td className="td-compact">{item.debutants != null ? formatInt(item.debutants) : "—"}</td>}
                  {show("first_here") && (
                    <td className="td-compact">
                      {item.first_at_location != null ? formatInt(item.first_at_location) : "—"}
                    </td>
                  )}
                  {show("guests") && (
                    <td className="td-compact">{item.guests != null ? formatInt(item.guests) : "—"}</td>
                  )}
                  {show("prs") && <td className="td-compact">{item.prs != null ? formatInt(item.prs) : "—"}</td>}
                  {show("best_male") && (
                    <td className="td-compact">
                      {formatTime(item.best_male_time_display, item.best_male_time_sec)}
                    </td>
                  )}
                  {show("best_female") && (
                    <td className="td-compact">
                      {formatTime(item.best_female_time_display, item.best_female_time_sec)}
                    </td>
                  )}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </TableWrap>
    </>
  );
}

function LastResultsContent() {
  // Вид — из адреса: ?week= открывает «За неделю» сразу на этой неделе, так
  // неделей можно поделиться, а «назад» на эту запись истории возвращает её же.
  // Старый ?date= из 3.9.0 открывает неделю своего дня.
  const [mode, setMode] = useState<ResultsMode>(() => (readWeekParam() ? "week" : "last"));
  const [chosenWeek, setChosenWeek] = useState<string | null>(readWeekParam);
  // Каждый вид грузит свои данные, только когда его открыли, и держит их
  // дальше: обратное переключение мгновенное, а ссылка на неделю не тянет за
  // собой витрину последних стартов (и наоборот — список недель за всю историю).
  const [lastWanted, setLastWanted] = useState(mode === "last");
  const [weeksWanted, setWeeksWanted] = useState(mode === "week");
  const isWeek = mode === "week";

  // Краткий вид набирает колонки по ширине экрана. Состояние живёт здесь, а не
  // в самой таблице: сегмент «Кратко | Полно» стоит в общей панели фильтров
  // над ней (правка Дмитрия 30.08.2026). Колонки у обоих видов одни и те же.
  const tableColumns = useTableColumns(LAST_RESULTS_COLUMNS);
  // Ответы и фильтры переживают уход и «назад» (см. hooks/useCachedResource,
  // hooks/useRestorableState): отсюда уходят в протокол и возвращаются.
  const lastResults = useCachedResource<LastResultsResponse | null>(
    lastWanted ? LAST_RESULTS_KEY : null,
    () => (lastWanted ? sharedLoad(LAST_RESULTS_KEY, getLastResults) : Promise.resolve(null)),
    [lastWanted],
    { errorText: "Не удалось загрузить результаты" },
  );
  const weeksResource = useCachedResource<LastResultsWeeks | null>(
    weeksWanted ? WEEKS_KEY : null,
    () => (weeksWanted ? sharedLoad(WEEKS_KEY, getLastResultsWeeks) : Promise.resolve(null)),
    [weeksWanted],
    { errorText: "Не удалось загрузить список недель" },
  );
  const weeks = weeksResource.data;
  // Неделя не выбрана — открываем последнюю неделю со стартами.
  const week = chosenWeek ?? weeks?.latest_saturday ?? weeks?.weeks.at(-1)?.saturday ?? null;
  const weekResource = useCachedResource<LastResultsWeekResponse | null>(
    week ? weekKey(week) : null,
    () => (week ? sharedLoad(weekKey(week), () => getLastResultsWeek(week)) : Promise.resolve(null)),
    [week],
    { errorText: "Не удалось загрузить старты недели" },
  );
  // Пока едет новая неделя, в data ещё лежит прежняя: её строки под новой
  // неделей выдавали бы чужие цифры за эту. Подгруженную заранее неделю берём
  // из кэша в том же кадре: хук скопирует её к себе только эффектом, и на кадр
  // мелькнула бы «Загрузка…».
  const weekData =
    weekResource.data && weekResource.data.saturday === week
      ? weekResource.data
      : week
        ? (readCached<LastResultsWeekResponse>(weekKey(week)) ?? null)
        : null;

  // Заодно переписывает старый ?date= и не субботний ?week= на субботу недели.
  useEffect(() => {
    writeWeekParam(isWeek ? week : null);
  }, [isWeek, week]);

  // Таблица держит место на экране. Перед сменой вида или недели запоминаем,
  // где стоит её верх, а после отрисовки возвращаем его туда же прокруткой.
  // Шапка и панель у видов одной высоты, так что обычно двигать нечего; это
  // страховка на остаток. Цель держим, пока не приедут свежие строки: сдвиг
  // может случиться и тогда (ошибка над таблицей, перенос ряда фильтров).
  //
  // Вторая половина — нижняя опора. Неделя в одну-две строки делает страницу
  // короче экрана, браузер сам прокручивает её к началу, и таблица уезжает
  // вниз на всю прокрутку. Поэтому область таблицы до следующей смены не
  // короче, чем нужно, чтобы её низ доставал до низа экрана.
  const tableAreaRef = useRef<HTMLDivElement | null>(null);
  const anchorTop = useRef<number | null>(null);
  const [holdHeight, setHoldHeight] = useState<number | null>(null);
  const rememberTablePosition = useCallback(() => {
    const box = tableAreaRef.current?.getBoundingClientRect();
    if (!box) {
      anchorTop.current = null;
      return;
    }
    anchorTop.current = box.top;
    setHoldHeight(Math.max(0, Math.round(window.innerHeight - box.top)));
  }, []);

  // Повтор после ошибки — тем же жестом, что привёл к ней: снова выбрать эту
  // неделю или снова открыть вид. Ключ кэша при этом не меняется, и сам хук
  // второй раз не спросил бы.
  // 404 хук отдаёт флагом notFound, а не текстом ошибки. Здесь это та же
  // ошибка: ручки нет (выкат на полпути — фронт уже новый, api ещё старый), и
  // без неё страница так и осталась бы приглушённой с «Загрузкой…» без повтора.
  const lastError =
    lastResults.error ?? (lastResults.notFound ? "Не удалось загрузить результаты" : null);
  const weeksError =
    weeksResource.error ?? (weeksResource.notFound ? "Не удалось загрузить список недель" : null);
  const weekError =
    weekResource.error ?? (weekResource.notFound ? "Не удалось загрузить старты недели" : null);
  const reloadWeek = weekResource.reload;
  const weekFailed = weekError !== null;
  const weeksFailed = weeksError !== null;
  const reloadWeeks = weeksResource.reload;
  const showWeek = useCallback(
    (next: string | null) => {
      rememberTablePosition();
      // Суббота — единственный адрес недели: любой другой её день ведёт туда же.
      const target = next ? saturdayOfWeek(next) : null;
      if (target) {
        setChosenWeek(target);
      }
      if ((target === null || target === week) && weekFailed) {
        reloadWeek();
      }
      if (weeksFailed) {
        reloadWeeks();
      }
      setMode("week");
      setWeeksWanted(true);
    },
    [rememberTablePosition, week, weekFailed, reloadWeek, weeksFailed, reloadWeeks],
  );
  const switchMode = (next: ResultsMode) => {
    if (next === mode) {
      return;
    }
    if (next === "week") {
      showWeek(null);
      return;
    }
    rememberTablePosition();
    if (lastError) {
      lastResults.reload();
    }
    setMode("last");
    setLastWanted(true);
  };
  const retry = () => {
    if (isWeek) {
      if (weekFailed) {
        reloadWeek();
      }
      if (weeksFailed) {
        reloadWeeks();
      }
    } else {
      lastResults.reload();
    }
  };

  // Пока едут строки нового вида или недели, на экране остаются прежние —
  // приглушённые. Пустое место с «Загрузка…» на полсекунды схлопывало
  // страницу и раскрывало её снова, и смена вида выглядела как новая страница.
  const fresh = isWeek ? (weekData?.items ?? null) : (lastResults.data?.items ?? null);
  const freshKey = isWeek ? `week:${week ?? ""}` : "last";
  const shownRef = useRef<{ items: LastResultsItem[]; key: string } | null>(null);
  useEffect(() => {
    if (fresh) {
      shownRef.current = { items: fresh, key: freshKey };
    }
  }, [fresh, freshKey]);
  // Без выбранной недели она берётся из списка недель — не загрузился список,
  // не загрузится и неделя, и это та же ошибка.
  const error = isWeek ? (weekError ?? (week ? null : weeksError)) : lastError;
  // Ошибка — не ожидание: чужие строки под ней остались бы приглушёнными
  // навсегда с «Загрузкой…» над ними.
  const shown = fresh ? { items: fresh, key: freshKey } : error ? null : shownRef.current;
  const stale = !fresh && shown !== null;
  const items = shown?.items ?? null;
  // Фильтр «неактивных» — по виду тех строк, что на экране: приглушённые строки
  // прежнего вида не должны на миг разрастись на скрытые локации.
  const shownIsWeek = shown ? shown.key.startsWith("week:") : isWeek;

  useLayoutEffect(() => {
    const before = anchorTop.current;
    const area = tableAreaRef.current;
    if (before === null || !area) {
      return;
    }
    const shift = area.getBoundingClientRect().top - before;
    if (Math.abs(shift) >= 1) {
      window.scrollBy({ top: shift, behavior: "instant" });
    }
    if (!stale) {
      anchorTop.current = null;
    }
  }, [mode, week, stale, shown?.key, error]);
  const saturdayDate = lastResults.data?.saturday_date ?? null;
  // Витрина подписана последней субботой, а без суббот — последним днём:
  // ссылка всё равно ведёт в его неделю.
  const latestWeek = saturdayDate ? saturdayOfWeek(saturdayDate) : null;

  const [query, setQuery] = useRestorableState("results.query", "");
  // Мультивыбор: систем можно отметить сколько угодно, пустое множество —
  // «Все» (правка Дмитрия 01.09.2026; раньше выбиралась ровно одна).
  // Множество в снимке записи не переживёт JSON — держим массив, а Set строим.
  const [platformList, setPlatformList] = useRestorableState<string[]>("results.platforms", []);
  const [showPaused, setShowPaused] = useRestorableState("results.paused", false);
  // Кнопка parkrun горит, только когда его строки есть на экране (с учётом
  // «неактивных» в «Последнем старте»). Отметка parkrun переживает переход на
  // неделю без него, но фильтрует только при горящей кнопке: иначе таблица
  // опустела бы без видимой причины, а «Все» при этом горело бы.
  const parkrunShown = (items ?? []).some(
    (item) =>
      item.event_platform_codes.includes("parkrun") &&
      (shownIsWeek || showPaused || !(item.is_paused || item.is_cancelled)),
  );
  const platforms = useMemo(
    () => new Set(platformList.filter((code) => code !== "parkrun" || parkrunShown)),
    [platformList, parkrunShown],
  );
  const setPlatforms = useCallback(
    (next: Set<string>) => setPlatformList([...next]),
    [setPlatformList],
  );

  const filtered = useMemo(() => {
    if (!items) {
      return [];
    }
    const normalizedQuery = query.trim().toLowerCase();
    return items.filter((item) => {
      if (normalizedQuery && !matchesQuery(item, normalizedQuery)) {
        return false;
      }
      if (platforms.size > 0 && !item.event_platform_codes.some((code) => platforms.has(code))) {
        return false;
      }
      // В «За неделю» — все, кто на той неделе стартовал: локация, с тех пор
      // ушедшая на паузу, тогда всё равно бежала (метка в строке остаётся).
      if (!shownIsWeek && !showPaused && (item.is_paused || item.is_cancelled)) {
        return false;
      }
      return true;
    });
  }, [items, query, platforms, showPaused, shownIsWeek]);

  const filteredFinishers = useMemo(
    () => filtered.reduce((sum, item) => sum + (item.finishers ?? 0), 0),
    [filtered],
  );
  // В «За неделю» строка — старт, и локаций бывает меньше строк: суббота и
  // праздник в будни у одной локации.
  const filteredLocations = useMemo(
    () => new Set(filtered.map((item) => item.identity_key)).size,
    [filtered],
  );
  const weekIsEmpty = isWeek && weekData !== null && weekData.total === 0;

  // Строка над таблицей — единственное, что в шапке таблицы зависит от вида,
  // и она всегда в одну строку: высота блока над таблицей не меняется.
  let countLine: ReactNode;
  if (stale) {
    countLine = "Загрузка…";
  } else if (isWeek) {
    countLine = weekIsEmpty ? (
      "На этой неделе стартов не было"
    ) : (
      <>
        {pluralizeRu(filteredLocations, COUNT_FORMS.locations)},{" "}
        {pluralizeRu(filteredFinishers, COUNT_FORMS.finishers)}
        {/* Стартов больше, чем локаций, — значит, кто-то бежал и в будни.
            На телефоне хвост прячется (CSS): в одну строку он не влезает, а
            многоточие оставило бы «· 302 ст…». Сами строки видны в таблице. */}
        {filtered.length > filteredLocations && (
          <span className="results-count-starts">
            {" · "}
            {pluralizeRu(filtered.length, COUNT_FORMS.events)}
          </span>
        )}
      </>
    );
  } else {
    countLine = (
      <>
        {pluralizeRu(filtered.length, COUNT_FORMS.locations)}
        {latestWeek && (
          <>
            {" · "}
            <WeekLink
              saturday={latestWeek}
              onSelect={showWeek}
              title="Все старты этой недели"
            >
              {/* На узком экране — «неделя 29.08 →»: строка не переносится,
                  и многоточие иначе съедало бы дату. */}
              <span className="results-count-wide">старты недели {formatDate(latestWeek)} →</span>
              <span className="results-count-narrow">неделя {splitYear(latestWeek)[0]} →</span>
            </WeekLink>
          </>
        )}
      </>
    );
  }

  return (
    <PortalSectionShell sidebar={{ active: "last-results" }}>
      {/* Шапка у обоих видов одна и та же: всё, что зависит от вида, живёт в
          панели фильтров и строке над таблицей. Иначе при смене вида шапка
          меняла высоту и сдвигала всё ниже (правка Дмитрия 21.10.2026). */}
      <header className="loc-header">
        <div className="loc-header-title">
          <h1>Результаты стартов</h1>
        </div>
        <p className="muted loc-header-place">
          Финишёры, волонтёры, лучшие времена и новички 5 вёрст, С95 и RunPark: последний старт
          каждой локации или все старты выбранной недели.
        </p>
        {/* Соседний срез тех же данных: не «сколько на каждой локации»,
            а «кто и с каким временем» — вся страна одним протоколом. Неделя
            у протокола та же, что здесь: пн–вс, подписана субботой. */}
        <p className="loc-header-place">
          <a href={isWeek && week ? unifiedProtocolWeekHref(week) : "/protocol"}>
            Единый протокол недели — все финишёры страны по времени →
          </a>
        </p>
      </header>

      <section className="card loc-section loc-wide-page">
        <FilterPanel>
          <FilterRow>
            {/* Наведение на переключатель заранее подгружает «За неделю»:
                к клику таблица недели уже в кэше. display: contents — обёртка
                только ловит события, в раскладке ряда её нет. */}
            <div
              className="results-mode-intent"
              onPointerEnter={isWeek ? undefined : prefetchWeekMode}
              onFocus={isWeek ? undefined : prefetchWeekMode}
              onTouchStart={isWeek ? undefined : prefetchWeekMode}
            >
              <FilterGroup label="Вид">
                <FilterTabs
                  options={MODE_TABS}
                  value={mode}
                  onChange={switchMode}
                  ariaLabel="Вид результатов"
                  asTablist
                />
              </FilterGroup>
            </div>
            {/* Одно место на двоих: «неактивные» в «Последнем старте», выбор
                недели — в «За неделю». Ширина у места постоянная, поэтому ряд
                фильтров переносится одинаково в обоих видах. «Только суббота»
                ушла: её заменил второй вид. */}
            <FilterGroup label={isWeek ? "Неделя" : "Показывать"} className="results-mode-slot">
              {isWeek ? (
                weeks && week ? (
                  <WeekNav weeks={weeks.weeks} week={week} onSelect={showWeek} />
                ) : (
                  <div className="results-week-nav">
                    {weeksFailed ? (
                      <button type="button" className="results-retry" onClick={reloadWeeks}>
                        Список недель не загрузился — повторить
                      </button>
                    ) : (
                      <span className="muted">Загрузка…</span>
                    )}
                  </div>
                )
              ) : (
                <div className="loc-index-filters">
                  <label className="loc-index-paused">
                    <input
                      type="checkbox"
                      checked={showPaused}
                      onChange={(event) => setShowPaused(event.target.checked)}
                    />{" "}
                    неактивные
                  </label>
                </div>
              )}
            </FilterGroup>
            <PlatformFilter
              mode="multi"
              value={platforms}
              onChange={setPlatforms}
              options={PLATFORM_FILTERS.map((code) =>
                code === "parkrun" && !parkrunShown
                  ? {
                      code,
                      label: platformCodeLabel(code),
                      disabled: true,
                      title: isWeek
                        ? "На этой неделе стартов parkrun нет"
                        : "parkrun в России не проводится с 2022 года — его локации видны с «неактивными»",
                    }
                  : { code, label: platformCodeLabel(code) },
              )}
            />
            {tableColumns.hasToggle && (
              <FilterGroup label="Колонки">
                <TableViewToggle columns={tableColumns} inline />
              </FilterGroup>
            )}
            <FilterGroup label="Поиск" trailing>
              <FilterSearch
                value={query}
                onChange={setQuery}
                placeholder="Поиск: название, город или регион"
              />
            </FilterGroup>
          </FilterRow>
        </FilterPanel>

        {/* Ошибка — внутри области таблицы, а не над ней: карточка сверху
            сдвигала бы таблицу. */}
        <div
          ref={tableAreaRef}
          className={`results-table-area${stale ? " is-stale" : ""}`}
          aria-busy={stale || undefined}
          style={holdHeight ? { minHeight: holdHeight } : undefined}
        >
          {error && (
            <div className="card error results-error">
              <p>{error}</p>
              <button type="button" className="results-retry" onClick={retry}>
                Повторить
              </button>
            </div>
          )}
          {items && shown ? (
            <>
              <p className="muted loc-index-count">{countLine}</p>
              <LastResultsTable
                mode={mode}
                items={filtered}
                rowsKey={shown.key}
                emptyText={weekIsEmpty ? "На этой неделе стартов не было" : "Нет локаций по фильтрам"}
                tableColumns={tableColumns}
              />
            </>
          ) : (
            !error && <p className="muted">{isWeek && weeks && !week ? "Стартов пока нет" : "Загрузка…"}</p>
          )}
        </div>
      </section>
      <ScrollToTopButton />
    </PortalSectionShell>
  );
}

// Страница открыта без логина: посадочная под запросы «5 вёрст результаты».
export function LastResultsPage() {
  return <LastResultsContent />;
}
