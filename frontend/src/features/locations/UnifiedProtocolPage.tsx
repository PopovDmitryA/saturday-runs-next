import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { normalizeAppPath } from "../../hooks/useAppPath";
import { useCachedResource } from "../../hooks/useCachedResource";
import { useRestorableState } from "../../hooks/useRestorableState";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { Pagination } from "../../components/Pagination";
import { PlatformBadge } from "../../components/PlatformBadge";
import { ScrollToTopButton } from "../../components/ScrollToTopButton";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { TableWrap } from "../../components/tableUx/TableWrap";
import {
  FilterGroup,
  FilterPanel,
  FilterRow,
  FilterSearch,
  FilterSelect,
  FilterTabs,
} from "../../components/filters/FilterPanel";
import { GenderFilter } from "../../components/filters/GenderFilter";
import { PlatformFilter } from "../../components/filters/PlatformFilter";
import { TableViewToggle } from "../../components/tableUx/TableViewToggle";
import { useTableColumns } from "../../components/tableUx/useTableColumns";
import type { AdaptiveColumn } from "../../components/tableUx/useAdaptiveColumns";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { formatTemp, temperatureTone } from "../../lib/weather";
import { WeatherChip } from "../../components/WeatherChip";
import { PromoLoginCard } from "../../components/PromoLoginCard";
import { useOptionalUser } from "../../lib/useOptionalUser";
import {
  getUnifiedProtocol,
  getUnifiedProtocolWeeks,
  type UnifiedProtocol,
  type UnifiedProtocolRow,
  type UnifiedProtocolSort,
  type UnifiedProtocolWeekRef,
} from "../../lib/api";
import { formatDate, formatInt } from "../../lib/format";
import { currentEntryKey, pushSameEntry } from "../../lib/historyEntry";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { flushMetrikaHit } from "../../lib/metrika";

type GenderFilter = "all" | "male" | "female";

export type UnifiedProtocolParams = {
  /** Любой день недели; без даты — последняя неделя с данными. */
  saturday: string | null;
};

const PER_PAGE = 100;

/**
 * Что открыто в протоколе: зачёт, поиск, порядок и страница.
 *
 * Всё это живёт в адресе (карточка бэклога 03.10.2026): ссылку на «медленные сверху,
 * страница 37» или «где 1000-е место» можно отправить и открыть в новой
 * вкладке, а «Назад» и «Вперёд» браузера листают страницы протокола. Значения
 * по умолчанию в адрес не пишем — у недели один «голый» адрес. Для поисковика
 * все эти варианты — одна страница: canonical у неё без строки запроса
 * (lib/pageMeta, seo_service), как у фильтров рейтингов.
 */
type ProtocolView = {
  platform: string | null;
  gender: GenderFilter;
  ageGroup: string | null;
  query: string;
  sort: UnifiedProtocolSort;
  page: number;
  /** «К месту N»: страницу, где стоит это место, выбирает сервер. */
  place: number | null;
};

const DEFAULT_VIEW: ProtocolView = {
  platform: null,
  gender: "all",
  ageGroup: null,
  query: "",
  sort: "fast",
  page: 1,
  place: null,
};

/** Ответ вместе с ключом среза, под который его просили (см. fresh ниже). */
type ShownProtocol = UnifiedProtocol & { viewKey: string };

/** Целое от единицы из адреса или поля ввода; мусор — null. */
function positiveInt(raw: string | null): number | null {
  if (!raw || !/^\d{1,7}$/.test(raw)) {
    return null;
  }
  const value = Number(raw);
  return value >= 1 ? value : null;
}

function readView(): ProtocolView {
  const params = new URLSearchParams(window.location.search);
  const gender = params.get("gender");
  const place = positiveInt(params.get("place"));
  return {
    platform: params.get("platform") || null,
    gender: gender === "male" || gender === "female" ? gender : "all",
    ageGroup: params.get("age") || null,
    query: (params.get("q") ?? "").trim(),
    sort: params.get("sort") === "slow" ? "slow" : "fast",
    // Место важнее номера страницы: страницу под него подберёт сервер.
    page: place === null ? (positiveInt(params.get("page")) ?? 1) : 1,
    place,
  };
}

function viewSearch(view: ProtocolView): string {
  const params = new URLSearchParams();
  if (view.sort !== "fast") params.set("sort", view.sort);
  if (view.place !== null) {
    params.set("place", String(view.place));
  } else if (view.page > 1) {
    params.set("page", String(view.page));
  }
  if (view.platform) params.set("platform", view.platform);
  if (view.gender !== "all") params.set("gender", view.gender);
  if (view.ageGroup) params.set("age", view.ageGroup);
  if (view.query) params.set("q", view.query);
  const search = params.toString();
  return search ? `?${search}` : "";
}

export function unifiedProtocolHref(saturday: string | null, view: ProtocolView = DEFAULT_VIEW): string {
  return `${saturday ? `/protocol/${saturday}` : "/protocol"}${viewSearch(view)}`;
}

/**
 * Строка запроса текущей записи истории — под показанный срез, без новой
 * записи. В ней только срез протокола: чужие метки ссылки Метрика посчитала
 * ещё при загрузке (init из сниппета), дальше в пересылаемой ссылке они лишние.
 * Якорь не трогаем.
 */
function replaceSearch(search: string): void {
  if (window.location.search !== search) {
    window.history.replaceState(
      window.history.state,
      "",
      `${window.location.pathname}${search}${window.location.hash}`,
    );
  }
}

/** Ключ ответа в кэше вкладки: неделя и весь срез. */
function protocolViewKey(saturday: string | null, view: ProtocolView): string {
  return [
    "protocol:week",
    saturday ?? "latest",
    view.platform ?? "",
    view.gender,
    view.ageGroup ?? "",
    view.query,
    view.sort,
    view.place === null ? `p${view.page}` : `at${view.place}`,
  ].join(":");
}

/**
 * Поле «к месту N»: ввёл 1000 — открылась страница, где стоит 1000-е место,
 * и строка подсвечена. Кейсы из карточки бэклога — граница ТОП-1000 и ТОП-10000.
 */
function PlaceJump({
  value,
  onJump,
  onClear,
}: {
  value: number | null;
  onJump: (place: number) => void;
  onClear: () => void;
}) {
  const [text, setText] = useState(value === null ? "" : String(value));
  // Место сменилось не отсюда («Назад», сброс фильтром) — поле показывает адрес.
  useEffect(() => {
    setText(value === null ? "" : String(value));
  }, [value]);
  const target = positiveInt(text);
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (target !== null) {
      onJump(target);
    }
  };
  return (
    <form className="uniprot-place-jump" onSubmit={submit}>
      <input
        className="fp-search uniprot-place-input"
        type="text"
        inputMode="numeric"
        enterKeyHint="go"
        autoComplete="off"
        placeholder="№ места"
        aria-label="Номер места в протоколе"
        value={text}
        // Только цифры: «1 000» и «№1000» из буфера обмена — тоже 1000.
        // Стёр число — переход снят: подсветка уходит, страница остаётся.
        onChange={(event) => {
          const next = event.target.value.replace(/\D+/g, "").slice(0, 7);
          setText(next);
          if (next === "" && value !== null) {
            onClear();
          }
        }}
      />
      {value !== null && (
        <button
          type="button"
          className="uniprot-place-clear"
          aria-label="Сбросить переход к месту"
          title="Сбросить переход к месту"
          onClick={onClear}
        >
          ×
        </button>
      )}
      <button type="submit" className="uniprot-place-go" disabled={target === null}>
        Перейти
      </button>
    </form>
  );
}

/** «00:18:08» → «18:08» — часовых времён на пятёрке не бывает. */
function stripHours(display: string | null): string {
  if (!display) {
    return "—";
  }
  return display.replace(/^00:/, "");
}

/** 🥇🥈🥉 первым трём — видно сразу, без наведения. */
function medal(place: number | null): string | null {
  if (place === 1) return "🥇";
  if (place === 2) return "🥈";
  if (place === 3) return "🥉";
  return null;
}

/** В возрастной группе — только золото: групп под сотню, три медали в каждой
 *  превратили бы страницу в ёлку. */
function groupMedal(place: number | null): string {
  return place === 1 ? "🥇" : "";
}

function StatTile({
  value,
  label,
  sub,
  hint,
}: {
  value: string | number | null;
  label: string;
  sub?: ReactNode;
  hint?: string;
}) {
  return (
    <div className="stat-card loc-stat-card">
      <span className="stat-value loc-stat-value">
        {value ?? "—"}
        {hint && (
          <StatHintTooltip text={hint}>
            <span className="loc-stat-info" aria-label="Как считается">
              i
            </span>
          </StatHintTooltip>
        )}
      </span>
      <span className="stat-label">{label}</span>
      {sub && <span className="loc-stat-sub">{sub}</span>}
    </div>
  );
}

// Колонки в порядке важности; место, участник и время — минимум краткого вида.
// Ширины — зеркало CSS-классов .uniprot-table .col-*.
const UNIFIED_COLUMNS: AdaptiveColumn[] = [
  { key: "place", width: 67, required: true },
  { key: "runner", width: 168, required: true },
  { key: "time", width: 96, required: true },
  { key: "location", width: 208 },
  { key: "gender_place", width: 122 },
  { key: "age_group", width: 184 },
  { key: "platform", width: 109 },
  { key: "pace", width: 88 },
  { key: "location_position", width: 115 },
  { key: "club", width: 168 },
];

/** Подпись на подсвеченной строке, когда запрошенного места нет. */
type NearestNote = { label: string; hint: string };

function RunnerCell({ row, nearest = null }: { row: UnifiedProtocolRow; nearest?: NearestNote | null }) {
  return (
    <span className="protocol-runner">
      {row.serial_id !== null ? (
        <a href={`/users/${row.serial_id}`}>{row.name ?? "—"}</a>
      ) : (
        <span>{row.name ?? "—"}</span>
      )}
      {nearest && (
        <StatHintTooltip text={nearest.hint}>
          <span className="protocol-row-badge uniprot-badge-nearest">{nearest.label}</span>
        </StatHintTooltip>
      )}
      {row.is_me && <span className="protocol-row-badge protocol-badge-me">вы</span>}
      {row.is_first_run && (
        <StatHintTooltip text="Первый старт в системе">
          <span className="protocol-row-badge">новичок</span>
        </StatHintTooltip>
      )}
      {row.is_pr && (
        <StatHintTooltip text="Личный рекорд в системе на этом старте">
          <span className="protocol-row-badge">ЛР</span>
        </StatHintTooltip>
      )}
    </span>
  );
}

function LocationCell({ row }: { row: UnifiedProtocolRow }) {
  const title = row.location_name || "—";
  return (
    <span className="uniprot-location">
      {row.location_slug ? (
        <a href={`/locations/${encodeURIComponent(row.location_slug)}`}>{title}</a>
      ) : (
        <span>{title}</span>
      )}
      {row.city && row.city !== title && <span className="muted"> · {row.city}</span>}
    </span>
  );
}

const EMPTY_WEEKS: UnifiedProtocolWeekRef[] = [];

/** Куда докрутить, когда приедет нужная страница: к строке места или к таблице. */
type ScrollTarget = "row" | "table";

/**
 * Намерение докрутки вместе со срезом, под который оно заказано. Тратится
 * только на ответ под этот срез: при быстром двойном «Назад» отложенный эффект
 * прошлой отрисовки (соседняя страница из кэша) забирал «к строке» себе, строки
 * не находил и уводил к началу таблицы, а пришедшую страницу с местом
 * докручивать было уже некому (ревью 21.10.2026).
 */
type PendingScroll = { target: ScrollTarget; view: ProtocolView } | null;

function UnifiedProtocolContent({ saturday }: UnifiedProtocolParams) {
  // undefined — сессия ещё проверяется: пока не знаем, баннер не мигаем.
  const viewer = useOptionalUser();
  // Срез, порядок и страница — из адреса (см. ProtocolView). «Назад» из
  // профиля возвращает ту же запись истории, а с ней и тот же адрес.
  const [view, setView] = useState<ProtocolView>(readView);
  // Последний выбранный срез — для обработчиков, которые живут дольше рендера
  // (popstate, пауза в наборе поиска).
  const viewRef = useRef(view);
  // Текст в поле поиска отдельно от view.query: запрос уходит после паузы в наборе.
  const [search, setSearch] = useState(view.query);
  const { platform, gender, ageGroup, query, sort, page, place } = view;
  const viewKey = protocolViewKey(saturday, view);
  // Ответ по срезу — из кэша вкладки, свежий подъезжает следом (см. hooks/useCachedResource).
  const {
    data,
    error,
    loading,
    reload,
  } = useCachedResource<ShownProtocol>(
    viewKey,
    async () => ({
      ...(await getUnifiedProtocol(saturday, {
        platform,
        gender: gender === "all" ? null : gender,
        ageGroup,
        q: query || null,
        sort,
        page,
        place,
        perPage: PER_PAGE,
      })),
      viewKey,
    }),
    [saturday, platform, gender, ageGroup, query, sort, page, place],
    { errorText: "Не удалось загрузить протокол", onSettled: flushMetrikaHit },
  );
  // Пока новый срез грузится, на экране стоит прежний ответ (так таблица не
  // мигает пустотой). Подсветка места, докрутка и правка адреса смотрят только
  // на ответ, пришедший именно под текущий срез.
  const fresh = data !== null && data.viewKey === viewKey;
  // Неделя на экране — для адреса без даты (см. navigate).
  const shownWeekRef = useRef<string | null>(null);
  shownWeekRef.current = data?.saturday ?? null;
  const { data: weeksData } = useCachedResource("protocol:weeks", getUnifiedProtocolWeeks, []);
  const weeks = weeksData?.weeks ?? EMPTY_WEEKS;
  const tableSectionRef = useRef<HTMLElement | null>(null);
  const attachFloatingHead = useFloatingTableHead(".tview-bar");
  const tableColumns = useTableColumns(UNIFIED_COLUMNS);
  const show = tableColumns.show;
  const showFull = tableColumns.showFull;

  // Пришли по ссылке прямо в глубину протокола (?place=, ?page=) — докрутим к
  // таблице, как только приедет ответ. Только при первой сборке записи
  // истории: следующая — это возврат «назад» или F5, там позицию возвращает
  // lib/scrollMemory, и докрутка спорила бы с ней.
  const [seen, setSeen] = useRestorableState("uniprot.seen", false);
  const pendingScroll = useRef<PendingScroll>(
    seen ? null : place !== null ? { target: "row", view } : page > 1 ? { target: "table", view } : null,
  );
  useEffect(() => {
    setSeen(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const scrollToTable = useCallback((behavior: ScrollBehavior) => {
    tableSectionRef.current?.scrollIntoView({ behavior, block: "start" });
  }, []);

  // К строке места (или ближайшего, если такого нет), а нет её на странице —
  // к началу таблицы.
  const scrollToPlace = useCallback(
    (focus: number | null) => {
      const row =
        focus !== null ? tableSectionRef.current?.querySelector(`tr[data-place="${focus}"]`) : null;
      if (row) {
        row.scrollIntoView({ behavior: "smooth", block: "center" });
      } else {
        scrollToTable("smooth");
      }
    },
    [scrollToTable],
  );

  useEffect(() => {
    const pending = pendingScroll.current;
    if (!data || !fresh || pending === null || data.viewKey !== protocolViewKey(saturday, pending.view)) {
      return;
    }
    pendingScroll.current = null;
    if (pending.target === "row") {
      scrollToPlace(data.focus_place);
    } else {
      scrollToTable("smooth");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fresh, data]);

  /**
   * Сменить срез или страницу. Страница таблицы и «к месту» — новая запись
   * истории («Назад» листает обратно), но с тем же ключом (pushSameEntry):
   * страница не пересобирается, плитки и колонки стоят на месте. Фильтры и
   * порядок правят текущую запись — как фильтры рейтингов
   * (LeaderboardPage.writeBoardFilters).
   *
   * Адрес без даты (/protocol) — «последняя неделя», и с первым стартом новой
   * недели он поведёт на неё. Страница и место имеют смысл только внутри своей
   * недели, поэтому шаг к ним закрепляет в адресе неделю на экране: ссылка
   * «вот граница ТОП-1000», отправленная в воскресенье, и в пятницу откроет
   * того же человека. Роутер узнаёт о новом пути от pushSameEntry, а страница
   * не пересобирается (см. UnifiedProtocolPage ниже). Фильтры и порядок без
   * страницы остаются «последней неделей» — такую ссылку и хотят держать.
   *
   * Докрутку заказывает последний шаг (scroll): прежнее намерение относилось
   * к другому срезу, и его ответа больше не ждём.
   */
  const navigate = useCallback(
    (next: ProtocolView, mode: "push" | "replace", scroll: ScrollTarget | null = null) => {
      pendingScroll.current = scroll === null ? null : { target: scroll, view: next };
      const search = viewSearch(next);
      if (mode === "push") {
        const week = shownWeekRef.current;
        const path =
          week && normalizeAppPath() === "/protocol" ? `/protocol/${week}` : window.location.pathname;
        pushSameEntry(`${path}${search}`);
      } else {
        replaceSearch(search);
      }
      viewRef.current = next;
      setView(next);
    },
    [],
  );

  // Срез поменялся — страница и «к месту» сбрасываются: в новом зачёте место N
  // занимает другой человек. Повторный клик по выбранному ничего не сбрасывает.
  const refine = useCallback(
    (patch: Partial<ProtocolView>) => {
      const current = viewRef.current;
      const changed = (Object.keys(patch) as (keyof ProtocolView)[]).some(
        (name) => patch[name] !== current[name],
      );
      if (changed) {
        navigate({ ...current, ...patch, page: 1, place: null }, "replace");
      }
    },
    [navigate],
  );

  // Смена системы обнуляет возрастную группу: ступени у систем разные, а у S95
  // категорий нет вовсе — иначе фильтр оставался бы висеть невидимым и
  // протокол пустовал бы без объяснений.
  const choosePlatform = useCallback(
    (next: string | null) => {
      if (next !== viewRef.current.platform) {
        refine({ platform: next, ageGroup: null });
      }
    },
    [refine],
  );

  // Порядок — не срез: места те же. Но переход «к месту» он снимает и
  // открывает первую страницу: «Медленные» показывают самых медленных. Раньше
  // открытое место переживало разворот, и после «К месту 1092» кнопка
  // «Медленные» возвращала к той же строке — казалось, что ничего не
  // поменялось (ревью 22.10.2026).
  const chooseSort = useCallback(
    (next: UnifiedProtocolSort) => {
      const current = viewRef.current;
      if (next === current.sort) {
        return;
      }
      navigate({ ...current, sort: next, page: 1, place: null }, "replace");
    },
    [navigate],
  );

  // Сброс «к месту»: подсветка снимается, а страница остаётся той, где
  // стояло место, — человек смотрит на неё и не хочет улететь в начало.
  const clearPlace = useCallback(() => {
    const current = viewRef.current;
    if (current.place === null) {
      return;
    }
    const shownPage =
      data !== null && data.viewKey === protocolViewKey(saturday, current) ? data.page : current.page;
    navigate({ ...current, place: null, page: shownPage }, "replace");
  }, [navigate, data, saturday]);

  const goToPage = useCallback(
    (next: number) => {
      const current = viewRef.current;
      if (next !== current.page || current.place !== null) {
        navigate({ ...current, page: next, place: null }, "push");
      } else if (error) {
        // Эту страницу уже просили, и запрос упал (на экране прежняя, ссылка на
        // эту ещё жива): клик по ней — повтор запроса, а не вторая такая же
        // запись в истории.
        reload();
      }
      scrollToTable("smooth");
    },
    [navigate, scrollToTable, error, reload],
  );

  const jumpToPlace = useCallback(
    (target: number) => {
      const current = viewRef.current;
      if (target !== current.place) {
        navigate({ ...current, page: 1, place: target }, "push", "row");
        return;
      }
      // То же место ещё раз. Ответ сверяем с адресом (viewRef), а не с прошлым
      // рендером: пока новый ответ в пути, на экране прежний, и двойное
      // «Перейти» уводило к его строке, а пришедшую строку уже не докручивало.
      if (data !== null && data.viewKey === protocolViewKey(saturday, current)) {
        // Ответ под это место уже на экране — докручиваем без запроса.
        pendingScroll.current = null;
        scrollToPlace(data.focus_place);
        return;
      }
      // Ответ ещё в пути — докрутит эффект, когда он придёт.
      pendingScroll.current = { target: "row", view: current };
      if (error) {
        // Запрос под это место упал: «Перейти» с тем же числом — повтор.
        reload();
      }
    },
    [navigate, scrollToPlace, data, error, reload, saturday],
  );

  // Поиск по имени дёргает сервер — ждём паузы в наборе.
  useEffect(() => {
    const trimmed = search.trim();
    if (trimmed === viewRef.current.query) {
      return;
    }
    const timer = window.setTimeout(() => refine({ query: trimmed }), 350);
    return () => window.clearTimeout(timer);
  }, [search, refine]);

  // «Назад»/«Вперёд» между страницами протокола: запись та же (pushSameEntry),
  // App страницу не пересобирает — адрес перечитываем сами.
  useEffect(() => {
    const ownKey = currentEntryKey();
    const onPop = () => {
      // Ушли на другую запись истории — её соберёт App, здесь делать нечего.
      if (currentEntryKey() !== ownKey) {
        return;
      }
      const next = readView();
      // Закрылось окно поверх страницы (поиск, «Меню») — адрес тот же.
      if (viewSearch(next) === viewSearch(viewRef.current)) {
        return;
      }
      viewRef.current = next;
      setView(next);
      setSearch(next.query);
      // Намерение — под пришедший срез: ответ прошлой отрисовки, если его
      // эффект доиграет уже после этого «Назад», докрутку не заберёт.
      pendingScroll.current = next.place !== null ? { target: "row", view: next } : null;
      if (next.place === null && (tableSectionRef.current?.getBoundingClientRect().top ?? 0) < 0) {
        // Листали внизу таблицы — показываем начало пришедшей страницы.
        scrollToTable("auto");
      }
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [scrollToTable]);

  // Адрес — под то, что реально показано: систему, которой нет в неделе
  // (мусор в набранной руками ссылке или parkrun в неделе без parkrun), сервер
  // молча игнорирует, а страницу за концом протокола заменяет последней.
  // Возрастную группу сервер не проверяет: чужая или выдуманная (?age=) давала
  // пустой протокол, а select, не найдя её среди вариантов, показывал «Все
  // группы» — и выбрать их, чтобы снять фильтр, было нельзя. Снимаем сами.
  //
  // Правим только по ответу под срез, который в адресе сейчас (viewRef), а не
  // под тот, с которым рисовали: после быстрого «Назад» отложенный эффект
  // прошлой отрисовки видит уже новый адрес, но свой старый ответ — и вписал
  // бы в новую запись чужую страницу.
  useEffect(() => {
    if (!data || !fresh || data.viewKey !== protocolViewKey(saturday, viewRef.current)) {
      return;
    }
    const current = viewRef.current;
    const platformGone = current.platform !== null && data.scope_platform !== current.platform;
    const ageGone =
      current.ageGroup !== null && !data.age_groups.some((group) => group.age_group === current.ageGroup);
    if (platformGone || ageGone) {
      // Место в адресе остаётся — и строку докрутим уже в исправленном срезе:
      // с выдуманной группой (?place=1000&age=zzz) протокол был пуст, и
      // докрутка по первому ответу уходила к началу таблицы.
      navigate(
        {
          ...current,
          platform: platformGone ? data.scope_platform : current.platform,
          ageGroup: ageGone ? null : current.ageGroup,
        },
        "replace",
        current.place !== null ? "row" : null,
      );
      return;
    }
    // Дальше — только адрес (и его копия для обработчиков): ответ уже на
    // экране, перезапрашивать его под другой ключ незачем. Страницу за концом
    // протокола сервер заменил последней. Мусор (?sort=xxx, ?page=-5,
    // ?page=1.5, ?place=0, ?gender=xxx) readView уже заменил умолчаниями, а
    // ?page рядом с ?place — страницей места; в адресе он оставался, и ссылка,
    // которой поделятся, несла его дальше (ревью 21.10.2026).
    if (current.place === null && data.page !== current.page) {
      viewRef.current = { ...current, page: data.page };
    }
    replaceSearch(viewSearch(viewRef.current));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fresh, data]);

  const weekOptions = useMemo(() => [...weeks].reverse(), [weeks]);
  // Листая недели, систему и порядок обычно хотят удержать; остальной срез и
  // страница — свои у каждой недели.
  const weekView: ProtocolView = { ...DEFAULT_VIEW, platform, sort };

  // Полноэкранная ошибка — только пока нечего показывать. Когда данные уже
  // есть (упал запрос при листании или смене фильтра), страница остаётся:
  // карточка ошибки выводится над таблицей, а фильтры и пагинация живы —
  // ими же и повторяется запрос.
  if (error && !data) {
    return (
      <PortalSectionShell sidebar={{ active: "unified-protocol" }}>
        <div className="card error">
          <p>{error}</p>
        </div>
      </PortalSectionShell>
    );
  }

  if (!data) {
    return (
      <PortalSectionShell sidebar={{ active: "unified-protocol" }}>
        <p className="muted">Собираем протокол недели…</p>
      </PortalSectionShell>
    );
  }

  const summary = data.summary;
  const scopeTitle =
    data.platforms.find((item) => item.platform_code === data.scope_platform)?.title ?? null;
  // Подпись зачёта собирается из всех трёх ограничений: «5 вёрст · женщины ·
  // 40–44». Она же объясняет, почему «№» в таблице начинается с единицы.
  const scopeParts = [
    scopeTitle,
    gender === "male" ? "мужчины" : gender === "female" ? "женщины" : null,
    ageGroup,
  ].filter(Boolean);
  // Подпись зачёта показываем только когда он сужен: «зачёт: все системы» —
  // пустой шум, а вот «5 вёрст · женщины · 40–44» объясняет, почему «№»
  // начинается с единицы.
  const scopeLabel = scopeParts.join(" · ");
  const scopeHintLabel = scopeLabel || "все системы";
  const genderCounts = data.gender_counts;
  const genderKnown = genderCounts.male + genderCounts.female > 0;
  const hasAgeGroups = data.age_groups.length > 0;
  const hasClubs = summary.clubs_count > 0;
  const hasPace = data.results.some((row) => row.pace_display);
  const visibleCount =
    3 +
    (show("location") ? 1 : 0) +
    (show("gender_place") ? 1 : 0) +
    (hasAgeGroups && show("age_group") ? 1 : 0) +
    (show("platform") ? 1 : 0) +
    (hasPace && show("pace") ? 1 : 0) +
    (show("location_position") ? 1 : 0) +
    (hasClubs && show("club") ? 1 : 0);
  // Строка, к которой перешли «к месту N». Подсвечена, пока место в адресе:
  // ссылкой «вот граница ТОП-1000» делятся, и пришедший видит, о ком речь.
  const focusPlace = fresh && place !== null ? data.focus_place : null;
  // Такого места нет («К месту 20000» при 13 146 финишёрах) — подсвечено
  // ближайшее. Пояснение над таблицей после докрутки к строке оставалось за
  // верхним краем экрана: в поле 20000, подсвечено 13 146, и почему — не
  // видно. Поэтому коротко повторяем его на самой строке (ревью 21.10.2026).
  const nearestNote: NearestNote | null =
    focusPlace !== null && place !== null && focusPlace !== place
      ? {
          label: `ближайшее к ${formatInt(place)}`,
          hint: `Места ${formatInt(place)} ${
            query ? "среди найденных" : "в этом зачёте"
          } нет — показано ближайшее, ${formatInt(focusPlace)}.`,
        }
      : null;
  const shownFrom = data.total ? (data.page - 1) * data.per_page + 1 : 0;
  const shownTo = Math.min(data.page * data.per_page, data.total);

  const isFocusRow = (row: UnifiedProtocolRow) => focusPlace !== null && row.place === focusPlace;

  const renderRow = (row: UnifiedProtocolRow, keyPrefix: string) => (
    <tr
      key={`${keyPrefix}-${row.place ?? "x"}-${row.platform_code}-${row.external_user_id ?? row.name ?? ""}`}
      className={
        [
          row.is_me ? "protocol-row-me" : row.is_unknown ? "protocol-row-unknown" : null,
          isFocusRow(row) ? "uniprot-row-focus" : null,
        ]
          .filter(Boolean)
          .join(" ") || undefined
      }
      data-place={row.place ?? undefined}
    >
      <td className="td-compact">
        {medal(row.place) ?? ""}
        {row.place ?? "—"}
      </td>
      <td>
        <RunnerCell row={row} nearest={isFocusRow(row) ? nearestNote : null} />
      </td>
      <td className="td-compact">{stripHours(row.finish_time_display)}</td>
      {show("location") && (
        <td>
          <LocationCell row={row} />
        </td>
      )}
      {show("gender_place") && (
        <td className="td-compact">
          {row.gender_place === null ? (
            "—"
          ) : (
            <span className="uniprot-place">
              {medal(row.gender_place) ?? ""}
              {row.gender === "female" ? "Ж" : "М"} {row.gender_place}
              <span className="muted"> / {formatInt(row.gender_total ?? 0)}</span>
            </span>
          )}
        </td>
      )}
      {hasAgeGroups && show("age_group") && (
        <td className="td-compact">
          {row.age_group === null ? (
            "—"
          ) : (
            <span className="uniprot-place">
              {groupMedal(row.age_group_place)}
              {row.age_category ?? row.age_group}
              {row.age_group_place !== null && (
                <span className="muted">
                  {" "}
                  · {row.age_group_place} / {formatInt(row.age_group_total ?? 0)}
                </span>
              )}
            </span>
          )}
        </td>
      )}
      {show("platform") && (
        <td className="td-compact">
          <PlatformBadge code={row.platform_code} />
        </td>
      )}
      {hasPace && show("pace") && <td className="td-compact">{row.pace_display ?? "—"}</td>}
      {show("location_position") && (
        <td className="td-compact">
          {row.location_position ?? "—"}
        </td>
      )}
      {hasClubs && show("club") && <td>{row.club_name ?? "—"}</td>}
    </tr>
  );

  return (
    <PortalSectionShell sidebar={{ active: "unified-protocol" }}>
      <header className="loc-header loc-wide-page">
        <p className="muted loc-header-breadcrumb">
          <a href="/results">← Результаты</a> / Единый протокол
        </p>
        <h1>Единый протокол — {formatDate(data.saturday)}</h1>
        <p className="protocol-subtitle">
          Все площадки всех систем за одну неделю, выстроенные по времени финиша.
        </p>
        {error && (
          <div className="card error">
            <p>{error} — показана предыдущая выборка, попробуйте ещё раз.</p>
          </div>
        )}
        <nav className="protocol-nav" aria-label="Выбор недели">
          {data.previous_saturday ? (
            <a href={unifiedProtocolHref(data.previous_saturday, weekView)}>
              ← {formatDate(data.previous_saturday)}
            </a>
          ) : (
            <span className="muted">первая неделя</span>
          )}
          {weekOptions.length > 1 && (
            <FilterSelect
              ariaLabel="Неделя"
              value={data.saturday}
              onChange={(value) => {
                window.location.href = unifiedProtocolHref(String(value) || null, weekView);
              }}
              options={weekOptions.map((week) => ({
                value: week.saturday,
                label: `${formatDate(week.saturday)} · ${formatInt(week.finishers)}`,
              }))}
            />
          )}
          {data.next_saturday ? (
            <a href={unifiedProtocolHref(data.next_saturday, weekView)}>
              {formatDate(data.next_saturday)} →
            </a>
          ) : (
            <span className="muted">последняя неделя</span>
          )}
        </nav>
      </header>

      <div className="loc-stats-grid protocol-stats-grid uniprot-stats-grid">
        <StatTile
          value={formatInt(summary.finishers)}
          label="финишёров"
          sub={
            genderKnown ? (
              // Раскладкой, а не строкой: «6 885 М · 4 713 Ж · 903 без данных»
              // сливалось в кашу и переносилось по случайному месту.
              <span className="uniprot-stat-split">
                <span className="uniprot-stat-split-part">
                  <b>{formatInt(summary.male)}</b> М
                </span>
                <span className="uniprot-stat-split-part">
                  <b>{formatInt(summary.female)}</b> Ж
                </span>
                {summary.unknown_gender > 0 && (
                  <span className="uniprot-stat-split-part uniprot-stat-split-rest">
                    <b>{formatInt(summary.unknown_gender)}</b> без данных
                    {summary.finishers > 0 &&
                      ` (${Math.round((summary.unknown_gender / summary.finishers) * 100)}%)`}
                  </span>
                )}
              </span>
            ) : null
          }
        />
        <StatTile
          value={formatInt(summary.locations)}
          label="локаций"
          sub={scopeLabel || null}
        />
        {/* Волонтёры — при старте, а не при результате: у волонтёрства нет ни
            времени, ни возрастной группы, поэтому цифра не сужается срезом. */}
        <StatTile
          value={formatInt(summary.volunteer_people)}
          label="волонтёров"
          sub={
            summary.volunteers > summary.volunteer_people
              ? `${formatInt(summary.volunteers)} волонтёрств`
              : null
          }
          hint="Люди, а не роли: за один старт волонтёр часто берёт несколько ролей"
        />
        <StatTile
          value={stripHours(summary.median_time_display)}
          label="медианное время"
          sub={summary.avg_time_display ? `среднее ${stripHours(summary.avg_time_display)}` : null}
          hint="Медиана: половина страны финишировала быстрее этого времени, половина — медленнее"
        />
        {gender !== "female" && (
          <StatTile
            value={stripHours(summary.best_male?.time_display ?? null)}
            label="лучшее время (М)"
            sub={
              summary.best_male
                ? `${summary.best_male.name ?? ""} · ${summary.best_male.location_name}`
                : null
            }
          />
        )}
        {gender !== "male" && (
          <StatTile
            value={stripHours(summary.best_female?.time_display ?? null)}
            label="лучшее время (Ж)"
            sub={
              summary.best_female
                ? `${summary.best_female.name ?? ""} · ${summary.best_female.location_name}`
                : null
            }
          />
        )}
        <StatTile
          value={formatInt(summary.debutants)}
          label="новичков"
          sub={
            summary.scope_finishers > 0
              ? `${Math.round((summary.debutants / summary.scope_finishers) * 100)}% финишёров`
              : null
          }
          hint="Первый старт в своей системе"
        />
        <StatTile
          value={formatInt(summary.prs)}
          label="личных рекордов"
          sub={
            summary.scope_finishers > 0
              ? `${Math.round((summary.prs / summary.scope_finishers) * 100)}% финишёров`
              : null
          }
          hint="Результат лучше всех прежних в своей системе"
        />
      </div>

      {data.weather && data.weather.locations_with_weather > 0 && (
        <section className="card loc-section uniprot-weather">
          <h2 className="section-title">
            Погода недели
            <StatHintTooltip text="Погода из архива Open-Meteo на время старта каждой локации недели. Крайности — среди локаций, где в эту субботу был старт; «по стране» — медиана температуры на старте.">
              <span className="loc-section-title-info" aria-label="Как считается">
                ⓘ
              </span>
            </StatHintTooltip>
          </h2>
          <div className="loc-stats-grid uniprot-weather-grid">
            {data.weather.temperature_median_c != null && (
              <div className="stat-card loc-stat-card">
                <span className={`stat-value loc-stat-value temp-${temperatureTone(data.weather.temperature_median_c)}`}>
                  {formatTemp(data.weather.temperature_median_c)}
                </span>
                <span className="stat-label">по стране на старте</span>
                <span className="loc-stat-sub muted">
                  {formatInt(data.weather.locations_with_weather)} локаций с погодой
                  {data.weather.rain_locations > 0 && ` · дождь на ${data.weather.rain_locations}`}
                </span>
              </div>
            )}
            {(
              [
                ["coldest", "🥶 холоднее всего", data.weather.coldest],
                ["warmest", "🔥 теплее всего", data.weather.warmest],
                ["wettest", "🌧️ мокрее всего", data.weather.wettest],
                ["windiest", "💨 ветренее всего", data.weather.windiest],
              ] as const
            ).map(([key, label, item]) =>
              item ? (
                <div className="stat-card loc-stat-card uniprot-weather-card" key={key}>
                  <span
                    className={`stat-value loc-stat-value temp-${
                      key === "coldest" || key === "warmest" ? temperatureTone(item.weather.temperature_c) : "none"
                    }`}
                  >
                    {key === "wettest"
                      ? `${(item.weather.precipitation_run_mm ?? 0).toFixed(1)} мм`
                      : key === "windiest"
                        ? `${Math.round(item.weather.wind_gusts_ms ?? 0)} м/с`
                        : formatTemp(item.weather.temperature_c)}
                  </span>
                  <span className="stat-label">{label}</span>
                  <span className="loc-stat-sub">
                    <a href={`/locations/${item.location_slug ?? ""}`}>{item.location_name}</a>
                  </span>
                  <span className="loc-stat-sub">
                    <WeatherChip
                      weather={item.weather}
                      locationSlug={item.location_slug}
                      locationName={item.location_name}
                      labelOnly
                    />
                  </span>
                </div>
              ) : null,
            )}
          </div>
        </section>
      )}


      {/* Анониму — на месте «Вашего результата» призыв войти: страница открыта
          всем, но найти в ней СЕБЯ можно только с привязанным профилем (тот же
          приём, что в рейтингах — RatingsLoginBanner). */}
      {viewer === null && (
        <PromoLoginCard
          className="uniprot-login-card"
          icon="🏁"
          title="А где здесь вы?"
          text="Войдите и привяжите профиль своей беговой системы — ваш результат этой недели поднимется наверх страницы и подсветится в протоколе: место по стране, среди своего пола и в возрастной группе."
        />
      )}

      {data.my_results.length > 0 && (
        <section className="loc-section">
          <h2>Ваш результат</h2>
          <TableWrap className="protocol-table-wrap">
            <table className="data-table uniprot-table">
              <thead>
                <tr>
                  <ColumnHeader label="№" filterable={false} />
                  <ColumnHeader label="Участник" filterable={false} />
                  <ColumnHeader label="Время" filterable={false} />
                  <ColumnHeader label="Локация" filterable={false} />
                  <ColumnHeader label="М/Ж" filterable={false} />
                  {hasAgeGroups && <ColumnHeader label="Возр. группа" filterable={false} />}
                  <ColumnHeader label="Система" filterable={false} />
                </tr>
              </thead>
              <tbody>
                {data.my_results.map((row) => (
                  <tr key={`me-${row.platform_code}-${row.place ?? "x"}`} className="protocol-row-me">
                    <td className="td-compact">{row.place ?? "—"}</td>
                    <td>
                      <RunnerCell row={row} />
                    </td>
                    <td className="td-compact">{stripHours(row.finish_time_display)}</td>
                    <td>
                      <LocationCell row={row} />
                    </td>
                    <td className="td-compact">
                      {row.gender_place === null
                        ? "—"
                        : `${row.gender === "female" ? "Ж" : "М"} ${row.gender_place} / ${formatInt(
                            row.gender_total ?? 0,
                          )}`}
                    </td>
                    {hasAgeGroups && (
                      <td className="td-compact">
                        {row.age_group_place === null
                          ? "—"
                          : `${row.age_category ?? row.age_group} · ${row.age_group_place} / ${formatInt(
                              row.age_group_total ?? 0,
                            )}`}
                      </td>
                    )}
                    <td className="td-compact">
                      <PlatformBadge code={row.platform_code} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </section>
      )}

      {hasAgeGroups && (
        <details className="card uniprot-groups-spoiler">
          <summary className="uniprot-groups-summary">
            Возрастные группы
            <span className="muted"> ({formatInt(data.age_groups.length)})</span>
            {ageGroup && <span className="uniprot-groups-chosen"> · выбрана {ageGroup}</span>}
            {/* Что со ступенями можно делать — иначе полосы читаются как
                картинка, а они кликабельные. */}
            <span className="uniprot-groups-howto">
              разверните и щёлкните по ступени — протокол пересоберётся внутри неё
            </span>
          </summary>
          <div className="uniprot-groups-body">
          <p className="muted uniprot-scope-note">
            Группа берётся из категории самого протокола, поэтому 5 вёрст и RunPark считаются в
            одних ступенях. У S95 категорий в протоколе нет, у parkrun вместо категории стоит age
            grade — их строки в групповом зачёте не участвуют.
          </p>
          <div className="protocol-age-groups">
            {data.age_groups.map((group) => {
              const max = Math.max(...data.age_groups.map((item) => item.total), 1);
              const active = ageGroup === group.age_group;
              return (
                <button
                  type="button"
                  className={
                    active ? "protocol-age-row uniprot-age-row active" : "protocol-age-row uniprot-age-row"
                  }
                  key={group.age_group}
                  aria-pressed={active}
                  onClick={() => refine({ ageGroup: active ? null : group.age_group })}
                  title={
                    active
                      ? "Снять фильтр по группе"
                      : `Оставить в протоколе только группу ${group.age_group}`
                  }
                >
                  <span className="protocol-age-label">{group.age_group}</span>
                  <span className="protocol-age-bar-track">
                    <span
                      className="protocol-age-bar protocol-age-bar-male"
                      style={{ width: `${(group.male / max) * 100}%` }}
                    />
                    <span
                      className="protocol-age-bar protocol-age-bar-female"
                      style={{ width: `${(group.female / max) * 100}%` }}
                    />
                  </span>
                  <span className="protocol-age-total">{formatInt(group.total)}</span>
                  <span className="protocol-age-gender muted">{formatInt(group.male)} М</span>
                  <span className="protocol-age-gender muted">{formatInt(group.female)} Ж</span>
                </button>
              );
            })}
          </div>
          </div>
        </details>
      )}

      <section className="loc-section" ref={tableSectionRef}>
        <h2>Протокол</h2>
        {/* Система, пол и возрастная группа — отдельные зачёты: что выберете,
            внутри того и пересчитаются места. Отдельной секцией «Зачёт» система
            стояла в стороне от остальных фильтров (правка Дмитрия 29.08.2026). */}
        <p className="muted uniprot-scope-note">
          Система, пол и возрастная группа — отдельные зачёты: что выберете, внутри того и
          пересчитаются места.
        </p>
        <FilterPanel>
          <FilterRow>
          {/* Зачёт: внутри выбранной системы места пересчитываются, поэтому
              выбор ровно один (mode="single"). */}
          <PlatformFilter
            mode="single"
            value={platform ?? "all"}
            onChange={(next) => choosePlatform(next === "all" ? null : next)}
            options={data.platforms.map((item) => ({
              code: item.platform_code,
              label: item.title,
              count: item.finishers,
            }))}
          />
          <GenderFilter
            value={gender}
            onChange={(next) => refine({ gender: next as GenderFilter })}
            options={[
              { value: "all", count: genderCounts.total },
              { value: "male", count: genderCounts.male, disabled: !genderKnown },
              { value: "female", count: genderCounts.female, disabled: !genderKnown },
            ]}
          />
          {hasAgeGroups && (
            <FilterGroup label="Возрастная группа">
            <FilterSelect
              ariaLabel="Фильтр по возрастной группе"
              value={ageGroup ?? ""}
              onChange={(value) => refine({ ageGroup: String(value) || null })}
              options={[
                { value: "", label: "Все группы" },
                ...data.age_groups.map((group) => ({
                  value: group.age_group,
                  label: `${group.age_group} (${formatInt(group.total)})`,
                })),
              ]}
            />
            </FilterGroup>
          )}
          {tableColumns.hasToggle && (
            <FilterGroup label="Колонки">
              <TableViewToggle columns={tableColumns} inline />
            </FilterGroup>
          )}
          <FilterGroup label="Поиск" trailing>
            <FilterSearch
              value={search}
              onChange={setSearch}
              placeholder="Имя, площадка или клуб"
              ariaLabel="Поиск по имени, площадке или клубу"
            />
          </FilterGroup>
          </FilterRow>
          {/* Порядок и «к месту» — навигация по протоколу, а не зачёт: места
              от них не меняются. Отдельным рядом, чтобы не путались с фильтрами. */}
          <FilterRow divided>
            <FilterGroup label="Порядок">
              <FilterTabs<UnifiedProtocolSort>
                ariaLabel="Порядок строк протокола"
                value={sort}
                onChange={chooseSort}
                // Подписи короткие: заголовок группы «Порядок» уже даёт
                // контекст, а «Медленные сверху» на телефоне в 360px обрезалась.
                options={[
                  { value: "fast", label: "Быстрые", title: "Быстрые сверху — обычный порядок протокола" },
                  {
                    value: "slow",
                    label: "Медленные",
                    title: "Самые медленные финиши первыми — места при этом настоящие",
                  },
                ]}
              />
            </FilterGroup>
            <FilterGroup label="К месту">
              <PlaceJump value={place} onJump={jumpToPlace} onClear={clearPlace} />
            </FilterGroup>
          </FilterRow>
          {loading && <span className="muted uniprot-total">Считаем…</span>}
        </FilterPanel>
        {nearestNote && <p className="muted uniprot-scope-note">{nearestNote.hint}</p>}
        {summary.skipped_foreign_parkrun > 0 && (
          <p className="muted uniprot-scope-note">
            Зарубежный parkrun в зачёт не входит ({formatInt(summary.skipped_foreign_parkrun)}{" "}
            {summary.skipped_foreign_parkrun === 1 ? "результат" : "результатов"}): по таким
            площадкам в базе не протокол, а результаты наших туристов, среди них попадаются
            junior parkrun на 2 км.
          </p>
        )}

        <TableWrap
          innerRef={attachFloatingHead}
          outerRef={tableColumns.measureRef}
          className="protocol-table-wrap"
          stickyFirstCol={showFull}
        >
          <table
            className={`data-table data-table-layout-fixed uniprot-table${
              showFull ? "" : " data-table-short"
            }`}
            style={{ minWidth: tableColumns.minWidth }}
          >
            <colgroup>
              <col className="col-number" />
              <col className="col-runner" />
              <col className="col-time" />
              {show("location") && <col className="col-location" />}
              {show("gender_place") && <col className="col-gender" />}
              {hasAgeGroups && show("age_group") && <col className="col-age" />}
              {show("platform") && <col className="col-platform" />}
              {hasPace && show("pace") && <col className="col-pace" />}
              {show("location_position") && <col className="col-local-place" />}
              {hasClubs && show("club") && <col className="col-club" />}
            </colgroup>
            <thead>
              <tr>
                <ColumnHeader
                  label="№"
                  hint={`Место в едином протоколе выбранного зачёта: ${scopeHintLabel}`}
                  filterable={false}
                />
                <ColumnHeader label="Участник" filterable={false} />
                <ColumnHeader label="Время" filterable={false} />
                {show("location") && <ColumnHeader label="Локация" filterable={false} />}
                {show("gender_place") && (
                  <ColumnHeader
                    label="М/Ж"
                    hint="Место среди своего пола за всю неделю своей системы — от выбранного среза не зависит"
                    filterable={false}
                  />
                )}
                {hasAgeGroups && show("age_group") && (
                  <ColumnHeader
                    label="Возр. группа"
                    hint="Категория из протокола и место в своей возрастной группе за всю неделю своей системы"
                    filterable={false}
                  />
                )}
                {show("platform") && <ColumnHeader label="Система" filterable={false} />}
                {hasPace && show("pace") && <ColumnHeader label="Темп" filterable={false} />}
                {show("location_position") && (
                  <ColumnHeader
                    label="У себя"
                    hint="Место на своей площадке — то, что стоит в протоколе системы"
                    filterable={false}
                  />
                )}
                {hasClubs && show("club") && <ColumnHeader label="Клуб" filterable={false} />}
              </tr>
            </thead>
            <tbody>
              {data.results.length === 0 ? (
                <tr>
                  <td colSpan={visibleCount} className="table-empty-cell">
                    <span className="muted">
                      {summary.finishers === 0
                        ? "Протоколов этой недели в базе пока нет"
                        : "Никого не нашлось по выбранным фильтрам"}
                    </span>
                  </td>
                </tr>
              ) : (
                data.results.map((row) => renderRow(row, "row"))
              )}
            </tbody>
          </table>
        </TableWrap>

        {/* Номера страниц — настоящие ссылки (?page=N с текущим срезом): их
            можно открыть в новой вкладке. Обычный клик листает без
            пересборки страницы (goToPage → pushSameEntry). Неделя в ссылке —
            всегда с датой, даже на /protocol: страница N — место внутри
            своей недели (см. navigate). */}
        <Pagination
          page={data.page}
          pages={data.pages}
          hrefForPage={(target) => unifiedProtocolHref(data.saturday, { ...view, page: target, place: null })}
          onNavigate={goToPage}
          label="Страницы протокола"
          summary={`Строки ${formatInt(shownFrom)}–${formatInt(shownTo)} из ${formatInt(data.total)}`}
        />
      </section>

      <ScrollToTopButton />
    </PortalSectionShell>
  );
}

export function UnifiedProtocolPage(params: UnifiedProtocolParams) {
  // Ключа по неделе нет. Другая неделя — это новая запись истории, и App
  // пересобирает страницу сам (Fragment key=entryKey). А шаг вглубь протокола
  // на адресе без даты (/protocol → /protocol/{дата}, см. navigate) — та же
  // неделя и та же запись: пересборка мигнула бы «Собираем протокол недели…»
  // вместо соседней страницы таблицы.
  return <UnifiedProtocolContent {...params} />;
}
