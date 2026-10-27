import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { readCached, writeCached } from "../../lib/dataCache";
import { useEntryKey } from "../../hooks/useEntryKey";
import { useRestorableState } from "../../hooks/useRestorableState";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { ScrollToTopButton } from "../../components/ScrollToTopButton";
import { PinnedMeBar } from "../../components/tableUx/PinnedMeBar";
import { TableViewToggle } from "../../components/tableUx/TableViewToggle";
import { TableWrap } from "../../components/tableUx/TableWrap";
import type { AdaptiveColumn } from "../../components/tableUx/useAdaptiveColumns";
import { useTableColumns } from "../../components/tableUx/useTableColumns";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { COUNT_FORMS, formatInt, pluralFormRu, pluralizeRu } from "../../lib/format";
import { useOptionalUser } from "../../lib/useOptionalUser";
import { surnameFirst } from "../../lib/personName";
import {
  getChallengeRating,
  getMyChallengeRow,
  type ChallengeActivity,
  type ChallengeMissingKind,
  type ChallengeRatingResponse,
  type ChallengeRatingRow,
  type ChallengeSlice,
  type MyChallengeRatingResponse,
  type MyChallengeRatingRow,
} from "./challengeRatingsApi";
import { RatingsLoginBanner } from "./RatingsLoginBanner";
import { ratingName } from "./ratingNames";
import { RatingBreadcrumb, RatingFilters } from "./RatingPageParts";
import "./leaderboards.css";

const PAGE_STEP = 100;

const PLATFORM_HINT =
  "Без фильтра считаются все системы вместе. С фильтром значение и дата " +
  "пересчитываются только по выбранной системе — это отдельный рейтинг, а не колонка.";

const REACHED_HINT =
  "День, когда участник впервые набрал своё текущее значение. При равном " +
  "значении выше тот, кто дошёл до него раньше, а если и день один — кто " +
  "раньше дошёл до предыдущих ступеней.";

const ACTIVITY_HINT =
  "«Активные» — только те, кто выходил на старт (пробежкой или волонтёром) за " +
  "последний год. Места пересчитываются заново: давно не бегающие не занимают " +
  "верх таблицы.";

const THIS_YEAR_HINT = "Сколько пробежек в текущем году.";

const ONGOING_HINT = "Серия ещё идёт: участник был на старте на этой или прошлой неделе.";

const CHAIN_HINT =
  "От какого до какого номера старта закрыта цепочка. Номер засчитывается в " +
  "любой системе: №4 на С95 закрывает то же число, что №4 на 5 вёрстах.";

const YEAR_HINT =
  "Год, в котором набран рекорд. Если такой счёт был в нескольких годах — " +
  "первый из них; при равном счёте выше тот, кто набрал его раньше.";

const MISSING_HINT: Record<ChallengeMissingKind, string> = {
  seconds: "Каких секунд на часах финиша ещё нет.",
  alphabet: "Каких букв ещё нет — из тех, с которых начинаются названия локаций этого среза.",
  calendar_days:
    "Каких дат календаря ещё нет — ближайшие первыми: сверху те, что можно закрыть " +
    "в ближайшие недели.",
};

// Сколько недостающих значений видно сразу, без «ещё».
const MISSING_INLINE: Record<ChallengeMissingKind, number> = { seconds: 6, calendar_days: 4, alphabet: 8 };

// Варианты, где фильтр «Активные» бессмыслен: в них по построению только те,
// кто выходил на старт недавно.
const ACTIVITY_FREE_VARIANTS = new Set(["this_year", "current"]);

const ACTIVITY_LABELS: Record<ChallengeActivity, string> = { all: "Все", active: "Активные за год" };

const MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

function InfoHint({ text }: { text: string }) {
  return (
    <StatHintTooltip text={text} className="lb-info-hint">
      <span aria-hidden="true">i</span>
    </StatHintTooltip>
  );
}

function toNextHint(forms: [string, string, string]): string {
  return (
    `Сколько ${forms[2]} не хватает, чтобы индекс вырос на единицу. При равном ` +
    "индексе выше тот, кому осталось меньше; дата набора индекса от этого не меняется."
  );
}

function formatReached(value: string | null | undefined, format: "date" | "year"): string {
  if (!value) {
    return "—";
  }
  if (format === "year") {
    return value.slice(0, 4);
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("ru-RU");
}

function formatChain(row: ChallengeRatingRow): string {
  if (row.chain_from == null || row.chain_to == null) {
    return "—";
  }
  return row.chain_from === row.chain_to ? `№${row.chain_from}` : `№${row.chain_from}–№${row.chain_to}`;
}

function formatMissing(kind: ChallengeMissingKind, value: number | string): string {
  if (kind === "seconds") {
    return `:${String(value).padStart(2, "0")}`;
  }
  if (kind === "alphabet") {
    return String(value);
  }
  const [month, day] = String(value).split("-").map(Number);
  return `${day} ${MONTHS_SHORT[month - 1] ?? ""}`;
}

/** Срез — в адресе: ссылкой на него делятся в чатах локаций. */
function readSlice(): ChallengeSlice {
  const params = new URLSearchParams(window.location.search);
  return {
    platform: params.get("platform") ?? "all",
    variant: params.get("variant") ?? "",
    activity: params.get("activity") === "active" ? "active" : "all",
  };
}

function writeSlice(slice: ChallengeSlice): void {
  const params = new URLSearchParams();
  if (slice.platform !== "all") {
    params.set("platform", slice.platform);
  }
  if (slice.variant) {
    params.set("variant", slice.variant);
  }
  if (slice.activity !== "all") {
    params.set("activity", slice.activity);
  }
  const query = params.toString();
  window.history.replaceState(null, "", query ? `?${query}` : window.location.pathname);
}

function ParticipantName({ row }: { row: ChallengeRatingRow }) {
  const name = surnameFirst(row.display_name) || "Участник";
  if (row.site_serial_id != null) {
    return (
      <a className="lb-name lb-name-link" href={`/users/${row.site_serial_id}`}>
        {name}
      </a>
    );
  }
  return <span className="lb-name">{name}</span>;
}

function RankDelta({ delta }: { delta: number | null }) {
  if (!delta) {
    return null;
  }
  const up = delta > 0;
  return (
    <span className={up ? "lb-rank-delta lb-rank-up" : "lb-rank-delta lb-rank-down"}>
      {up ? "▲" : "▼"}
      {Math.abs(delta)}
    </span>
  );
}

function DeltaSlot({ delta }: { delta: number }) {
  // Слот фиксированной ширины, как в остальных рейтингах: дельта недели не
  // сдвигает цифры в колонке.
  return <span className="lb-delta">{delta > 0 ? `+${formatInt(delta)}` : ""}</span>;
}

/**
 * Чего не хватает: несколько значений сразу и «ещё N», который раскрывает
 * остальное. Сервер присылает не больше 30 значений на строку — у новичка
 * «Круглому году» не хватает трёх сотен дат, а нужны ближайшие; о прочих
 * говорит хвост «и ещё N».
 */
function MissingCell({
  kind,
  row,
  expanded,
  onToggle,
}: {
  kind: ChallengeMissingKind;
  row: ChallengeRatingRow;
  expanded: boolean;
  onToggle: () => void;
}) {
  const values = row.missing ?? [];
  const total = row.missing_total ?? values.length;
  if (total === 0) {
    return <span className="lb-challenge-complete">Всё собрано</span>;
  }
  // Прятать за «ещё» одно-два значения незачем — короткий хвост виден целиком.
  const inline = values.length <= MISSING_INLINE[kind] + 2 ? values.length : MISSING_INLINE[kind];
  const shown = expanded ? values : values.slice(0, inline);
  const beyondStored = total - values.length;
  return (
    <span className="lb-challenge-missing">
      {shown.map((value) => (
        <span key={String(value)} className="lb-challenge-chip">
          {formatMissing(kind, value)}
        </span>
      ))}
      {!expanded && values.length > inline && (
        <button type="button" className="lb-challenge-more" onClick={onToggle} aria-expanded={false}>
          ещё {formatInt(total - shown.length)}
        </button>
      )}
      {!expanded && values.length <= inline && beyondStored > 0 && (
        <span className="lb-challenge-rest muted">и ещё {formatInt(beyondStored)}</span>
      )}
      {expanded && (
        <>
          {beyondStored > 0 && <span className="lb-challenge-rest muted">и ещё {formatInt(beyondStored)}</span>}
          <button type="button" className="lb-challenge-more" onClick={onToggle} aria-expanded>
            свернуть
          </button>
        </>
      )}
    </span>
  );
}

function valueWithUnit(value: number, forms: [string, string, string] | null): string {
  return forms ? pluralizeRu(value, forms) : formatInt(value);
}

/** Давно ли человек был на старте: год и больше до последнего старта базы. */
function staleYear(lastSeen: string | null | undefined, latest: string | null, activeDays: number): string | null {
  if (!lastSeen || !latest) {
    return null;
  }
  const gap = (Date.parse(latest) - Date.parse(lastSeen)) / 86_400_000;
  return gap > activeDays ? lastSeen.slice(0, 4) : null;
}

/** «Измайлово +1 · Терлецкий +2 · 2 новые локации по 6». */
function formatPlan(row: MyChallengeRatingRow, unit: [string, string, string]): string | null {
  const plan = row.to_next_plan;
  if (!plan || plan.length === 0) {
    return null;
  }
  return plan
    .map((step) =>
      step.location
        ? `${step.location} +${step.add}`
        : `${pluralizeRu(step.count ?? 1, ["новая локация", "новые локации", "новых локаций"])} по ${pluralizeRu(step.add, unit)}`,
    )
    .join(" · ");
}

type CachedChallenge = { data: ChallengeRatingResponse; me: MyChallengeRatingResponse | null };

export function ChallengeRatingPage({ slug }: { slug: string }) {
  // Ключ записи истории пересобирает страницу на каждом переходе: соседний
  // челлендж — тот же компонент, и без ключа на него утекали бы система,
  // поиск и таблица прежнего (так же устроен LeaderboardPage).
  const entryKey = useEntryKey();
  return <ChallengeBoard key={`${slug}:${entryKey}`} slug={slug} />;
}

function ChallengeBoard({ slug }: { slug: string }) {
  const [slice, setSliceState] = useState<ChallengeSlice>(readSlice);
  const currentUser = useOptionalUser();
  // Зрителя в ключе и в зависимостях загрузки держим по id, а не объектом:
  // useOptionalUser сначала отдаёт пользователя из sessionStorage, потом
  // свежий объект с тем же id — и таблица скачивалась дважды (аудит 27.10.2026).
  const viewerId = currentUser?.id ?? null;
  const cacheKey = `ratings:challenge:${slug}:${slice.platform}:${slice.variant}:${slice.activity}:${viewerId ?? "anon"}`;
  const [restored] = useState(() => readCached<CachedChallenge>(cacheKey));
  const [data, setData] = useState<ChallengeRatingResponse | null>(restored?.data ?? null);
  const [me, setMe] = useState<MyChallengeRatingResponse | null>(restored?.me ?? null);
  const [loading, setLoading] = useState(restored === undefined);
  const [error, setError] = useState<string | null>(null);
  const [visibleCount, setVisibleCount] = useRestorableState(`challenge.${slug}.visible`, PAGE_STEP);
  // Поиск просеивает уже полученные строки, места остаются общими.
  const [query, setQuery] = useRestorableState(`challenge.${slug}.query`, "");
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());

  const tableRef = useRef<HTMLTableElement | null>(null);
  const myRowRef = useRef<HTMLTableRowElement | null>(null);
  const attachFloatingHead = useFloatingTableHead(".tview-bar");

  const setSlice = useCallback((next: ChallengeSlice) => {
    writeSlice(next);
    setSliceState(next);
  }, []);

  /**
   * Вариант счёта решает и набор систем: у волонтёрской серии нет parkrun.
   * Выбранная система, которой в новом варианте нет, сбрасывается на «Все» —
   * иначе кнопка parkrun осталась бы нажатой над общим зачётом.
   */
  const chooseVariant = useCallback(
    (variant: string, platformOptions: string[]) => {
      setSlice({
        variant,
        platform: platformOptions.includes(slice.platform) ? slice.platform : "all",
        // «Этот год» и так состоит только из тех, кто бегал в этом году, —
        // фильтр «Активные» там ничего не меняет и сбрасывается.
        activity: ACTIVITY_FREE_VARIANTS.has(variant) ? "all" : slice.activity,
      });
    },
    [setSlice, slice.activity, slice.platform],
  );

  // Таблица — готовый снимок на сервере, ответ быстрый; строку «Вы» всё равно
  // спрашиваем после таблицы, как на соседних рейтингах.
  const load = useCallback(
    async (signal?: AbortSignal) => {
      const cached = readCached<CachedChallenge>(cacheKey);
      if (cached) {
        setData(cached.data);
        setMe(cached.me);
        setLoading(false);
      } else {
        setLoading(true);
        setMe(null);
      }
      setError(null);
      try {
        const payload = await getChallengeRating(slug, slice, signal);
        setData(payload);
        setLoading(false);
        let myRow: MyChallengeRatingResponse | null = null;
        if (viewerId) {
          myRow = await getMyChallengeRow(slug, slice, signal);
        }
        setMe(myRow);
        writeCached(cacheKey, { data: payload, me: myRow });
      } catch (loadError) {
        if (loadError instanceof DOMException && loadError.name === "AbortError") {
          return;
        }
        if (!cached) {
          setError(
            loadError instanceof Error && loadError.message !== "unauthorized"
              ? loadError.message
              : "Не удалось загрузить рейтинг",
          );
        }
        setLoading(false);
      }
    },
    [cacheKey, viewerId, slice, slug],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const listKey = `${slice.platform}|${slice.variant}|${slice.activity}|${query}`;
  const lastListKey = useRef(listKey);
  useEffect(() => {
    if (lastListKey.current !== listKey) {
      lastListKey.current = listKey;
      setVisibleCount(PAGE_STEP);
      setExpanded(new Set());
    }
  }, [listKey, setVisibleCount]);

  const toggleExpanded = useCallback((rowKey: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(rowKey)) {
        next.delete(rowKey);
      } else {
        next.add(rowKey);
      }
      return next;
    });
  }, []);

  const allRows = data?.rows ?? [];
  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) {
      return allRows;
    }
    return allRows.filter((row) => (row.display_name ?? "").toLowerCase().includes(needle));
  }, [allRows, query]);
  const searching = query.trim().length > 0;
  const myRowKey = me?.row?.row_key ?? null;
  const myIndex = useMemo(
    () => (myRowKey ? rows.findIndex((row) => row.row_key === myRowKey) : -1),
    [rows, myRowKey],
  );

  const showMyRow = useCallback(() => {
    if (myIndex < 0) {
      return;
    }
    setVisibleCount((count) =>
      myIndex >= count ? Math.ceil((myIndex + 1) / PAGE_STEP) * PAGE_STEP : count,
    );
    window.requestAnimationFrame(() => {
      myRowRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }, [myIndex]);

  // Колонки систем — только в общем зачёте: под фильтром системы колонка
  // была бы одна и повторяла бы значение.
  const platformColumns =
    data && data.platform === "all" ? data.platform_options.filter((code) => code !== "all") : [];
  const toNextForms = data?.to_next_forms ?? null;
  const missingKind = data?.missing_kind ?? null;
  const reachedFormat = data?.reached_format ?? "date";
  const showsChain = data?.shows_chain ?? false;
  const showDeltas = data?.show_deltas ?? true;
  const currentYear = data?.latest_event_date?.slice(0, 4) ?? null;
  // «В 2026» — у «Ударного года» по лучшему году; в варианте «Этот год» это и
  // есть само значение.
  const showsThisYear = data?.code === "best_year" && data.variant !== "this_year";
  const activeDays = data?.active_days ?? 365;
  // Ширины обязаны совпадать с CSS (.lb-challenge-table .lb-col-*).
  const columns = useMemo<AdaptiveColumn[]>(() => {
    const list: AdaptiveColumn[] = [
      { key: "rank", width: 99, required: true },
      { key: "name", width: 200, required: true },
      { key: "value", width: 96, required: true },
    ];
    if (toNextForms) {
      // У p- и V-индекса остаток — второй ключ места, поэтому он виден и в
      // кратком виде на телефоне (просьба Дмитрия 27.10.2026).
      list.push({ key: "to_next", width: 96, required: true });
    }
    if (showsChain) {
      list.push({ key: "chain", width: 120 });
    }
    if (showsThisYear) {
      list.push({ key: "this_year", width: 88 });
    }
    list.push({ key: "date", width: reachedFormat === "year" ? 72 : 112 });
    if (missingKind) {
      list.push({ key: "missing", width: 336 });
    }
    list.push({ key: "platforms", width: 80 * Math.max(platformColumns.length, 1) });
    return list;
  }, [missingKind, platformColumns.length, reachedFormat, showsChain, showsThisYear, toNextForms]);
  const tableColumns = useTableColumns(columns);
  const show = tableColumns.show;
  const showPlatforms = platformColumns.length > 0 && show("platforms");

  const visibleRows = rows.slice(0, visibleCount);
  const nextChunkEnd = Math.min(visibleCount + PAGE_STEP, rows.length);
  const defaultVariant = data?.variants[0]?.key ?? "";
  const activeFilters =
    (slice.platform !== "all" ? 1 : 0) +
    (slice.variant && slice.variant !== defaultVariant ? 1 : 0) +
    (slice.activity !== "all" ? 1 : 0) +
    (searching ? 1 : 0);
  const title = ratingName(slug, data ? { label: data.title, group: "Челленджи" } : undefined).label;
  const unitForms = data?.unit_forms ?? null;
  const reachedLabel = reachedFormat === "year" ? "Год" : "Набрано";
  const myRow = me?.row ?? null;

  return (
    <PortalSectionShell sidebar={{ active: "ratings" }}>
      <div className="lb-page">
        <RatingBreadcrumb ratingKey={slug} />

        {loading && !data && <p className="muted">Загружаем рейтинг…</p>}
        {error && !data && (
          <div className="lb-error">
            <p>{error}</p>
            <button type="button" className="btn btn-sm" onClick={() => void load()}>
              Повторить
            </button>
          </div>
        )}

        {data && (
          <div className={`lb-page-body${loading ? " lb-refreshing" : ""}`}>
            <header className="lb-header">
              <h1>
                <span aria-hidden="true">{data.icon}</span> {title}
              </h1>
              <p className="lb-description">{data.description}</p>
              <p className="lb-description muted">
                {data.ranking_note}
                {data.entrants > 0 &&
                  ` В зачёте ${pluralizeRu(data.entrants, COUNT_FORMS.participants)}, в таблице — первые ${formatInt(data.limit)}.`}
              </p>
            </header>

            <RatingsLoginBanner />

            <RatingFilters
              activeCount={activeFilters}
              aside={tableColumns.hasToggle ? <TableViewToggle columns={tableColumns} inline /> : null}
            >
              <div className="lb-fastest-controls">
                <div className="lb-fastest-controls-row">
                  {data.variants.length > 0 && (
                    <div className="lb-visits">
                      <span className="lb-visits-label">{data.variant_label ?? "Вариант"}</span>
                      <div className="lb-gender-tabs" role="group" aria-label={data.variant_label ?? "Вариант"}>
                        {data.variants.map((option) => (
                          <button
                            key={option.key}
                            type="button"
                            aria-pressed={data.variant === option.key}
                            className={`lb-gender-tab${data.variant === option.key ? " lb-gender-tab-active" : ""}`}
                            onClick={() => chooseVariant(option.key, option.platform_options)}
                          >
                            {option.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  <div className="lb-visits">
                    <span className="lb-visits-label">
                      Система <InfoHint text={PLATFORM_HINT} />
                    </span>
                    <div className="lb-gender-tabs" role="group" aria-label="Смотреть по системе">
                      {data.platform_options.map((value) => (
                        <button
                          key={value}
                          type="button"
                          aria-pressed={data.platform === value}
                          className={`lb-gender-tab${data.platform === value ? " lb-gender-tab-active" : ""}`}
                          onClick={() => setSlice({ ...slice, platform: value })}
                        >
                          {value === "all" ? "Все" : (data.platform_labels[value] ?? value)}
                        </button>
                      ))}
                    </div>
                  </div>
                  {!ACTIVITY_FREE_VARIANTS.has(data.variant) && (
                  <div className="lb-visits">
                    <span className="lb-visits-label">
                      Участники <InfoHint text={ACTIVITY_HINT} />
                    </span>
                    <div className="lb-gender-tabs" role="group" aria-label="Какие участники">
                      {(["all", "active"] as const).map((value) => (
                        <button
                          key={value}
                          type="button"
                          aria-pressed={data.activity === value}
                          className={`lb-gender-tab${data.activity === value ? " lb-gender-tab-active" : ""}`}
                          onClick={() => setSlice({ ...slice, activity: value })}
                        >
                          {ACTIVITY_LABELS[value]}
                        </button>
                      ))}
                    </div>
                  </div>
                  )}
                  {tableColumns.hasToggle && (
                    <div className="lb-visits lb-wide-only">
                      <span className="lb-visits-label">Колонки</span>
                      <TableViewToggle columns={tableColumns} inline />
                    </div>
                  )}
                  <div className="lb-visits lb-fastest-search">
                    <span className="lb-visits-label">Поиск в таблице</span>
                    <input
                      className="lb-search"
                      type="search"
                      placeholder="Имя участника…"
                      aria-label="Поиск по имени"
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                    />
                  </div>
                </div>
              </div>
            </RatingFilters>

            {data.scope_note && <p className="lb-meta muted">{data.scope_note}</p>}

            {!data.ready && (
              <p className="lb-meta muted">
                Рейтинг ещё считается — таблица появится после ближайшего пересчёта (раз в{" "}
                {data.refresh_hours} ч).
              </p>
            )}

            {searching && (
              <p className="lb-meta muted">
                {rows.length > 0
                  ? `Нашлось ${pluralizeRu(rows.length, COUNT_FORMS.participants)} из ${formatInt(
                      allRows.length,
                    )}. Места в таблице — общие по рейтингу.`
                  : `По запросу «${query.trim()}» в таблице никого нет.`}
              </p>
            )}

            {error && (
              <div className="lb-error">
                <p>{error}. В таблице ниже — прежний срез, не тот, что выбран фильтрами.</p>
                <button type="button" className="btn btn-sm" onClick={() => void load()}>
                  Повторить
                </button>
              </div>
            )}

            {myRow && (
              <section
                className={me?.included ? "lb-me" : "lb-me lb-me-out"}
                aria-label="Ваш результат в рейтинге"
              >
                <div className="lb-me-row">
                  <span className="lb-me-rank">{formatInt(myRow.rank)}</span>
                  <span className="lb-me-name">{surnameFirst(myRow.display_name) || "Вы"}</span>
                  <span className="lb-me-values">
                    <span className="lb-me-value">
                      <span className="lb-me-platform">{data.value_label}</span>
                      <span className="lb-challenge-value">
                        {valueWithUnit(myRow.value, unitForms)}
                        {myRow.ongoing && (
                          <span className="lb-challenge-ongoing" title={ONGOING_HINT}>
                            {" "}🔥 идёт
                          </span>
                        )}
                      </span>
                    </span>
                    {toNextForms && myRow.to_next != null && (
                      <span className="lb-me-value">
                        <span className="lb-me-platform">Осталось до следующего</span>
                        <span>{pluralizeRu(myRow.to_next, toNextForms)}</span>
                      </span>
                    )}
                    {toNextForms && formatPlan(myRow, toNextForms) && (
                      <span className="lb-me-value lb-me-value-wide">
                        <span className="lb-me-platform">Где добрать</span>
                        <span>{formatPlan(myRow, toNextForms)}</span>
                      </span>
                    )}
                    {showsThisYear && myRow.this_year != null && currentYear && (
                      <span className="lb-me-value">
                        <span className="lb-me-platform">В {currentYear}</span>
                        <span>{formatInt(myRow.this_year)}</span>
                      </span>
                    )}
                    {showsChain && (
                      <span className="lb-me-value">
                        <span className="lb-me-platform">Старты</span>
                        <span>{formatChain(myRow)}</span>
                      </span>
                    )}
                    <span className="lb-me-value">
                      <span className="lb-me-platform">{reachedLabel}</span>
                      <span>{formatReached(myRow.reached_on, reachedFormat)}</span>
                    </span>
                    {missingKind && myRow.missing_total != null && myRow.missing_total > 0 && (
                      <span className="lb-me-value lb-me-value-wide">
                        <span className="lb-me-platform">Не хватает</span>
                        <MissingCell
                          kind={missingKind}
                          row={myRow}
                          expanded={expanded.has(`me:${myRow.row_key}`)}
                          onToggle={() => toggleExpanded(`me:${myRow.row_key}`)}
                        />
                      </span>
                    )}
                  </span>
                  <div className="lb-me-actions">
                    {myIndex >= 0 && (
                      <button type="button" className="btn btn-ghost btn-sm" onClick={showMyRow}>
                        Показать в таблице
                      </button>
                    )}
                    {!me?.included && (
                      <p className="lb-me-percentile muted">
                        В таблице первые {formatInt(data.limit)} участников — вы пока ниже.
                      </p>
                    )}
                  </div>
                </div>
              </section>
            )}

            <TableWrap
              innerRef={attachFloatingHead}
              className={`lb-table-wrap${tableColumns.hasToggle ? "" : " lb-table-wrap-flat"}`}
              outerRef={tableColumns.measureRef}
            >
              <table
                ref={tableRef}
                className={`data-table lb-table lb-challenge-table${
                  tableColumns.showFull ? " lb-table-full" : " lb-table-short"
                }`}
                style={{ minWidth: tableColumns.showFull ? tableColumns.minWidth : "100%" }}
              >
                <thead>
                  <tr>
                    <th className="lb-col-rank">Место</th>
                    <th className="lb-col-name">Участник</th>
                    {showPlatforms &&
                      platformColumns.map((code) => (
                        <th key={code} className="lb-col-num">
                          {data.platform_labels[code] ?? code}
                        </th>
                      ))}
                    <th className="lb-col-num lb-col-total">{data.value_label}</th>
                    {toNextForms && show("to_next") && (
                      <th className="lb-col-num lb-col-to-next">
                        Осталось <InfoHint text={toNextHint(toNextForms)} />
                      </th>
                    )}
                    {showsThisYear && show("this_year") && currentYear && (
                      <th className="lb-col-num lb-col-this-year">
                        В {currentYear} <InfoHint text={THIS_YEAR_HINT} />
                      </th>
                    )}
                    {showsChain && show("chain") && (
                      <th className="lb-col-chain">
                        Старты <InfoHint text={CHAIN_HINT} />
                      </th>
                    )}
                    {show("date") && (
                      <th className="lb-col-date">
                        {reachedLabel} <InfoHint text={reachedFormat === "year" ? YEAR_HINT : REACHED_HINT} />
                      </th>
                    )}
                    {missingKind && show("missing") && (
                      <th className="lb-col-missing">
                        Не хватает <InfoHint text={MISSING_HINT[missingKind]} />
                      </th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.map((row, index) => {
                    const isMe = myRowKey != null && row.row_key === myRowKey;
                    return (
                      <tr
                        key={row.row_key}
                        ref={isMe && index === myIndex ? myRowRef : undefined}
                        className={isMe ? "lb-row-me" : undefined}
                      >
                        <td className="lb-col-rank">
                          <span className="lb-rank">
                            {formatInt(row.rank)}
                            {showDeltas && <RankDelta delta={row.rank_delta} />}
                          </span>
                        </td>
                        <td className="lb-col-name">
                          <ParticipantName row={row} />
                          {staleYear(row.last_seen, data.latest_event_date, activeDays) && (
                            <span className="lb-challenge-stale">
                              последний старт: {staleYear(row.last_seen, data.latest_event_date, activeDays)}
                            </span>
                          )}
                        </td>
                        {showPlatforms &&
                          platformColumns.map((code) => {
                            const value = row.platforms?.[code];
                            return (
                              <td key={code} className="lb-col-num">
                                <span className="lb-cell">
                                  {value ? formatInt(value) : <span className="lb-zero">—</span>}
                                </span>
                              </td>
                            );
                          })}
                        <td className="lb-col-num lb-col-total">
                          <span className="lb-cell lb-total">
                            {formatInt(row.value)}
                            {row.ongoing && (
                              <span className="lb-challenge-ongoing" title={ONGOING_HINT} aria-label="серия идёт">
                                🔥
                              </span>
                            )}
                            <DeltaSlot delta={showDeltas ? row.value_delta : 0} />
                          </span>
                        </td>
                        {toNextForms && show("to_next") && (
                          <td className="lb-col-num lb-col-to-next">
                            {row.to_next != null ? formatInt(row.to_next) : <span className="lb-zero">—</span>}
                          </td>
                        )}
                        {showsThisYear && show("this_year") && currentYear && (
                          <td className="lb-col-num lb-col-this-year">
                            {row.this_year ? formatInt(row.this_year) : <span className="lb-zero">—</span>}
                          </td>
                        )}
                        {showsChain && show("chain") && (
                          <td className="lb-col-chain">{formatChain(row)}</td>
                        )}
                        {show("date") && (
                          <td className="lb-col-date">{formatReached(row.reached_on, reachedFormat)}</td>
                        )}
                        {missingKind && show("missing") && (
                          <td className="lb-col-missing">
                            <MissingCell
                              kind={missingKind}
                              row={row}
                              expanded={expanded.has(row.row_key)}
                              onToggle={() => toggleExpanded(row.row_key)}
                            />
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableWrap>

            {rows.length > visibleCount && (
              <div className="lb-more">
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => setVisibleCount((count) => count + PAGE_STEP)}
                >
                  Показать ещё (места {visibleCount + 1}–{nextChunkEnd})
                </button>
              </div>
            )}

            {myRow && myIndex >= 0 && !searching && (
              <PinnedMeBar
                rowRef={myRowRef}
                tableRef={tableRef}
                rank={myRow.rank}
                name={myRow.display_name?.trim() || "Вы"}
                value={
                  unitForms
                    ? `${formatInt(myRow.value)} ${pluralFormRu(myRow.value, unitForms)}`
                    : formatInt(myRow.value)
                }
                onShow={showMyRow}
                watchKey={`${slice.platform}-${slice.variant}-${visibleCount}`}
              />
            )}
          </div>
        )}
        {/* Общая кнопка «наверх», как у остальных рейтингов: таблица здесь
            тоже на сотни строк. */}
        <ScrollToTopButton />
      </div>
    </PortalSectionShell>
  );
}
