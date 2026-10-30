import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ConfirmModal } from "../../components/ConfirmModal";
import { DetailModal } from "../../components/DetailModal";
import {
  ApiError,
  enableNotifications,
  getNotificationNudge,
  isSessionMissingError,
  snoozeNotificationNudge,
} from "../../lib/api";
import { platformCodeLabel } from "../../lib/format";
import { useOverlayFocus } from "../portal/nav/useOverlayFocus";
import { useOverlayHistory } from "../portal/nav/useOverlayHistory";
import {
  deletePlan,
  getLocationPlans,
  getLocationStartDates,
  getMySeries,
  getPlannerSettings,
  getSeriesPreview,
  getSpecialDates,
  savePlan,
  saveSeries,
  stopSeries,
  type PlanItem,
  type PlanLocation,
  type PlannerLocationOption,
  type PlannerSettings,
  type PlanSeries,
  type PlansElsewhereDate,
  type SeriesPreview,
  type SeriesSaveBody,
  type StartDateItem,
} from "./plannerApi";
import { addDays, moscowTodayIso, parseIsoDay, planDayLabel, startFacts, toIsoDay } from "./plannerDates";
import { PlanPlacePicker } from "./PlanPlacePicker";
import { PlanVisibilityToggle } from "./PlanVisibility";
import { runToText } from "./placeCase";
import { SeriesOptions } from "./SeriesOptions";
import { shortDays } from "./seriesText";
import {
  isSaturday,
  SERIES_ICON,
  seriesCoversDate,
  seriesNeedsRenewal,
  seriesSpanText,
  TripRemoveModal,
  type PlanModalOutcome,
} from "./seriesParts";
import "./planner.css";

/**
 * Окно «Планирую пробежать»: дата из календаря локации и «Публично | Приватно».
 * Заметок нет (решение Дмитрия 27.10.2026: перегружали интерфейс).
 * Выбора, чем заняться на старте, нет — план всегда «пробежать» (решение
 * Дмитрия 25.10.2026). Времени старта в окне нет — план про дату (решение
 * 26.10.2026).
 *
 * Два входа, и ни один не ведёт в другой (решение Дмитрия 26.10.2026):
 * - от локации (страница локации, правка в «Моих планах») — локация задана,
 *   дата выбирается чипами её календаря;
 * - от даты (день в «Моих планах», «Планирую» на «Обзоре») — дата задана (её
 *   можно сдвинуть стрелками), локацию ищут прямо в окне: турист расписывает
 *   два месяца, не уходя из календаря.
 *
 * Здесь же постоянный план «каждую субботу»: «Один раз | ↻ Каждую субботу»
 * на уровне даты (решение Дмитрия 27.10.2026: не рядом с локацией) и
 * ближайшие субботы галочками (вид V3, выбор 26.10.2026). Постоянный план
 * один на человека: на другой локации сервер спросит «Заменить?» (409).
 *
 * Порядок окна: дата → локация → «Публично | Приватно».
 *
 * Лимит дня — одно правило в обоих входах (решение 27.10.2026): на обычную
 * субботу план один, на особую дату — до трёх. Заполненный день выбрать
 * можно (правка Дмитрия 27.10.2026: «давать поставить, но упоминать, что
 * запланированный визит в первую локацию будет отменён»): под датами —
 * «План на сб 14 ноября (Кусково) будет отменён», кнопка — «Заменить план»,
 * и сервер в одной транзакции убирает тот план и ставит новый (replace_key).
 * На особую дату с тремя планами заменяется последний из них — окно его
 * называет. Поездка в другую локацию в субботу
 * постоянного плана его вытесняет; на особую дату постоянный план остаётся
 * (галочка «оставить», keep_series) — так «+ ещё локация» 1 января не
 * снимает дом.
 * Публичность — у каждого плана своя (у постоянного — одна на весь план);
 * переключатель стоит как профиль, отдельного согласия нет — показ планов
 * описан в общем согласии (решение Дмитрия 27.10.2026).
 *
 * После сохранения окно предлагает включить уведомления — без них
 * напоминание накануне старта не дойдёт — и уже потом зовёт onSaved.
 * Родитель закрывает окно и перезагружает данные в onSaved; plan — разовый
 * план (null — убран или сохранялся постоянный), outcome — что сказать в
 * снэкбаре.
 */

// Сколько ближайших дат видно сразу. Год вперёд — это полсотни суббот, и стена
// чипов прячет главное: люди планируют ближайшие недели. Особые даты видны
// всегда — ради них планировщик во многом и нужен. На компьютере — два ряда
// по четыре.
const VISIBLE_DATES = 8;

// Дальше чем на год вперёд план не ставится — как у сервера.
const HORIZON_DAYS = 365;

// Подпись даты живёт в plannerDates: её берут и лёгкие места (шапка профиля,
// «Обзор»), которым тянуть окно плана незачем. Отсюда — для прежних импортов.
export { planDayLabel };
export type { PlanModalOutcome };

type PlanMode = "once" | "series";

type PlanModalProps = {
  /**
   * Локация окна. null — окно «от даты»: дата задана (initialDate), локацию
   * выбирают в самом окне.
   */
  identityKey: string | null;
  locationName?: string | null;
  initialDate?: string;
  existing?: PlanItem | null;
  /** «series» — открыть сразу постоянный план («Изменить», «Продлить» у постоянного). */
  initialMode?: PlanMode;
  onClose: () => void;
  onSaved: (plan: PlanItem | null, outcome?: PlanModalOutcome) => void;
};

// form — само окно; nudge — вопрос про уведомления после сохранения; done —
// всё сказано, onSaved уже позван (родитель, как правило, окно размонтирует).
// conflict — сервер спросил «Заменить постоянный план?», stop — «Остановить?»,
// trip — убираем поездку, вытеснившую субботу постоянного.
type Stage = "form" | "nudge" | "done" | "conflict" | "stop" | "trip";

/** Выбранная локация окна: из пропсов (от локации) или из поиска (от даты). */
type Place = { key: string; name: string; option?: PlannerLocationOption };

function errorText(err: unknown, fallback: string): string {
  if (isSessionMissingError(err)) {
    return "Сессия закончилась — войдите снова";
  }
  return err instanceof Error && err.message ? err.message : fallback;
}

/** Тач-экран: поле поиска само не фокусируем — клавиатура закрыла бы подсказки. */
function coarsePointer(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(pointer: coarse)").matches;
}

/** Субботы от сегодня до горизонта и особые даты — дни, на которые вообще планируют. */
function startDays(todayIso: string, special: string[]): string[] {
  const days = new Set(special);
  const today = parseIsoDay(todayIso);
  const end = addDays(today, HORIZON_DAYS);
  for (let cursor = today; cursor <= end; cursor = addDays(cursor, 1)) {
    if (cursor.getDay() === 6) {
      days.add(toIsoDay(cursor));
    }
  }
  return [...days].filter((iso) => iso >= todayIso).sort();
}

function DateChip({
  item,
  selected,
  mine,
  regular,
  occupied,
  away,
  onSelect,
}: {
  item: StartDateItem;
  selected: boolean;
  mine: boolean;
  /** Дата уже в постоянном плане на этой локации. */
  regular: boolean;
  /**
   * Правим план, а на эту дату здесь уже стоит другой свой. Перенос туда
   * слил бы два плана в один: сервер правит план той же локации на дату.
   */
  occupied: boolean;
  /**
   * В этот день разовый план в другой локации — «бегу в Битцу»; blocked —
   * день заполнен (на субботу план один): выбрать можно, план заменит тот.
   */
  away: { text: string; blocked: boolean } | null;
  onSelect: () => void;
}) {
  const facts = startFacts(item);
  const special = item.kind === "special";
  const className = [
    "plan-date-chip",
    special ? "plan-date-chip-special" : "",
    selected ? "plan-date-chip-selected" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <button
      type="button"
      className={className}
      aria-pressed={selected}
      // Отменённый старт планировать незачем; свой план на нём всё равно
      // должен открываться — чтобы убрать.
      disabled={(item.cancelled && !selected && !mine) || occupied}
      onClick={onSelect}
    >
      <span className="plan-date-chip-day">{planDayLabel(item.date)}</span>
      {special && item.title && <span className="plan-date-chip-title">{item.title}</span>}
      {facts.length > 0 && <span className="plan-date-chip-meta">{facts.join(" · ")}</span>}
      {item.cancelled && <span className="plan-date-chip-cancel">старт отменён</span>}
      {occupied ? (
        <span className="plan-date-chip-mine">здесь уже есть ваш план</span>
      ) : away && !mine ? (
        <span className="plan-date-chip-meta">{away.text}</span>
      ) : mine ? (
        <span className="plan-date-chip-mine">✓ ваш план</span>
      ) : (
        regular && (
          <span className="plan-date-chip-mine">
            {SERIES_ICON} постоянный план
          </span>
        )
      )}
    </button>
  );
}

/** «‹ Сб 14 ноября ›» — дата окна «от даты»: стрелки шагают по дням стартов. */
function DateStepper({
  iso,
  title,
  prev,
  next,
  onStep,
}: {
  iso: string;
  title: string | null;
  prev: string | null;
  next: string | null;
  onStep: (iso: string) => void;
}) {
  return (
    <div className="plan-date-step">
      <button
        type="button"
        className="plan-date-step-btn"
        aria-label={prev ? `Предыдущий старт: ${planDayLabel(prev)}` : "Предыдущего старта нет"}
        disabled={!prev}
        onClick={() => prev && onStep(prev)}
      >
        ‹
      </button>
      <span className="plan-date-step-day" aria-live="polite">
        {planDayLabel(iso, true)}
        {title && <span className="plan-date-step-title">{title}</span>}
      </span>
      <button
        type="button"
        className="plan-date-step-btn"
        aria-label={next ? `Следующий старт: ${planDayLabel(next)}` : "Следующего старта нет"}
        disabled={!next}
        onClick={() => next && onStep(next)}
      >
        ›
      </button>
    </div>
  );
}

export function PlanModal({
  identityKey,
  locationName,
  initialDate,
  existing,
  initialMode,
  onClose,
  onSaved,
}: PlanModalProps) {
  const todayIso = useMemo(() => moscowTodayIso(), []);
  // Вход окна не меняется, пока оно открыто.
  const [fromDate] = useState(() => identityKey === null);
  const [place, setPlace] = useState<Place | null>(() =>
    identityKey ? { key: identityKey, name: locationName ?? "" } : null,
  );
  const placeKey = place?.key ?? null;
  const [stage, setStage] = useState<Stage>("form");
  const [dates, setDates] = useState<StartDateItem[] | null>(null);
  const [plannable, setPlannable] = useState(true);
  // «Скоро открытие»: дату первого старта человек сверяет на сайте системы.
  const [opening, setOpening] = useState<{ url: string | null } | null>(null);
  const [reason, setReason] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadVersion, setLoadVersion] = useState(0);
  // Свои планы на этой локации: выбрал дату, на которую план уже есть, —
  // окно становится правкой его публичности, а не вторым планом.
  // В правке другого плана такие даты закрыты: перенос туда слил бы два плана.
  const [mine, setMine] = useState<PlanItem[]>([]);
  // Разовые планы в других локациях по датам: «бегу в Битцу» у даты, а
  // заполненный день не выбрать — иначе о лимите дня человек узнавал бы по
  // ошибке после «Планирую» (ревью 26.10.2026, UI-3).
  const [elsewhere, setElsewhere] = useState<Map<string, PlansElsewhereDate>>(() => new Map());
  // С чего начинается «Публично | Приватно» и можно ли его трогать; не
  // загрузилось — переключатель всё равно есть, по умолчанию «Приватно».
  const [settings, setSettings] = useState<PlannerSettings | null>(null);
  const [settingsFailed, setSettingsFailed] = useState(false);
  // Выбор переключателя: null — не трогали, стоит как у плана (правка) или
  // как профиль (новый). У разового и постоянного — свой: «Каждую субботу»
  // правит публичность всего постоянного плана.
  const [oncePublic, setOncePublic] = useState<boolean | null>(existing?.is_public ?? null);
  const [seriesPublic, setSeriesPublic] = useState<boolean | null>(null);
  // Постоянный план здесь (правка) и действующий вообще (подсказки о поездке
  // и о замене — точный ответ всё равно даёт сервер).
  const [hereSeries, setHereSeries] = useState<PlanSeries | null>(null);
  const [activeSeries, setActiveSeries] = useState<PlanSeries | null>(null);
  const [mode, setMode] = useState<PlanMode>(existing ? "once" : (initialMode ?? "once"));
  const [preview, setPreview] = useState<SeriesPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  // Галочки ближайших суббот — снятая станет «Не бегаю».
  const [toggles, setToggles] = useState<Record<string, boolean>>({});
  const [conflict, setConflict] = useState<string | null>(null);
  const [stopBusy, setStopBusy] = useState(false);
  const [selected, setSelected] = useState<string | null>(existing?.plan_date ?? initialDate ?? null);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  // На особую дату постоянный план в другой локации остаётся: «+ ещё локация»
  // добавляет, а не заменяет (keep_series). Снятая галочка — поездка его вытеснит.
  const [keepSeries, setKeepSeries] = useState(true);
  // id плана, из которого заполнена форма (null — новая отметка).
  const [formFrom, setFormFrom] = useState<string | null>(existing?.id ?? null);
  const [showAll, setShowAll] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Перенос сохранил план на новой дате, а прежний убрать не смог. Отдельно
  // от error: смена даты или новая попытка не должны стирать это
  // предупреждение — иначе человек не узнает, что планов теперь два.
  const [moveError, setMoveError] = useState<string | null>(null);
  // Попросили дату, а в календаре локации её нет (особая дата чужой системы,
  // старт прошёл). Говорим об этом и не выбираем ничего: молча подставленная
  // ближайшая суббота одним нажатием ставила план не на тот день.
  const [missingDate, setMissingDate] = useState<string | null>(null);
  // Особые даты горизонта — для стрелок окна «от даты», пока локации нет.
  const [specialDays, setSpecialDays] = useState<{ date: string; title: string }[]>([]);
  // «изменить» у выбранной локации: поле поиска — с фокусом, клавиатура уже в деле.
  const [pickerFocus, setPickerFocus] = useState(() => fromDate && !coarsePointer());
  const changePlaceRef = useRef<HTMLButtonElement>(null);
  const [nudgeBusy, setNudgeBusy] = useState(false);
  const [nudgeNotice, setNudgeNotice] = useState<string | null>(null);
  const savedRef = useRef<PlanItem | null>(null);
  const outcomeRef = useRef<PlanModalOutcome | undefined>(undefined);

  // DetailModal держит onClose в зависимостях эффекта, который фиксирует body:
  // новая функция на каждый рендер дёргала бы прокрутку страницы под окном.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const onSavedRef = useRef(onSaved);
  onSavedRef.current = onSaved;
  const busy = saving || deleting || stopBusy;
  const busyRef = useRef(busy);
  busyRef.current = busy;
  const handleClose = useCallback(() => {
    if (busyRef.current) {
      return;
    }
    // План уже записан, хотя окно осталось на форме (перенос не убрал
    // прежний): закрытие — тоже «сохранено», иначе родитель не перечитает
    // данные и покажет старую картину без нового плана.
    if (savedRef.current || outcomeRef.current) {
      onSavedRef.current(savedRef.current, outcomeRef.current);
    } else {
      onCloseRef.current();
    }
  }, []);

  // «Назад» на телефоне закрывает окно, а не уводит со страницы с набранным
  // запросом (как у окон навигации, AGENTS §12; ревью 26.10.2026, UI-8).
  // Запись в истории — у формы, подтверждения поверх неё живут без записи.
  const stageRef = useRef(stage);
  stageRef.current = stage;
  const closeFromHistory = useCallback(() => {
    if (stageRef.current === "form") {
      handleClose();
    }
  }, [handleClose]);
  const { dismiss } = useOverlayHistory(stage === "form", closeFromHistory);
  const requestClose = useCallback(() => {
    if (!busyRef.current) {
      dismiss();
    }
  }, [dismiss]);

  // Фокус — в окно, Tab — по кругу внутри него, после закрытия — обратно на
  // кнопку, которой окно открыли: без этого фокус оставался на «Планирую» под
  // затемнением и Tab ходил по странице (ревью 27.10.2026, UI-4). Поле поиска
  // окна «от даты» фокус берёт само — его и оставляем. Esc закрывает
  // DetailModal.
  const panelRef = useRef<HTMLDivElement | null>(null);
  useOverlayFocus({
    open: stage === "form",
    containers: [panelRef],
    initial: () => {
      const panel = panelRef.current;
      const active = document.activeElement;
      if (panel && active instanceof HTMLElement && active !== panel && panel.contains(active)) {
        return active;
      }
      return (
        panel?.querySelector<HTMLElement>(".plan-date-chip[aria-pressed='true']") ??
        panel?.querySelector<HTMLElement>(".modal-close-btn") ??
        null
      );
    },
    onEscape: () => undefined,
  });
  // Окно открылось раньше дат — фокус встал на «×». Даты пришли — переводим
  // его на выбранную, один раз: дальше фокус ведёт человек.
  const datesLoaded = dates !== null && dates.length > 0;
  const chipFocusedRef = useRef(false);
  useEffect(() => {
    if (!datesLoaded || chipFocusedRef.current || stage !== "form") {
      return;
    }
    chipFocusedRef.current = true;
    const panel = panelRef.current;
    const frame = requestAnimationFrame(() => {
      if (panel && document.activeElement === panel.querySelector(".modal-close-btn")) {
        panel.querySelector<HTMLElement>(".plan-date-chip[aria-pressed='true']")?.focus();
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [datesLoaded, stage]);

  const mineByDate = useMemo(() => {
    const byDate = new Map<string, PlanItem>();
    for (const item of mine) {
      byDate.set(item.plan_date, item);
    }
    return byDate;
  }, [mine]);

  const planOn = useCallback(
    (iso: string | null): PlanItem | null => {
      if (!iso) {
        return null;
      }
      return mineByDate.get(iso) ?? (existing && existing.plan_date === iso ? existing : null);
    },
    [mineByDate, existing],
  );

  /**
   * Дата с разовым планом в другой локации: «бегу в Битцу» и заполнен ли день.
   * Свой план здесь на эту дату — правка, лимит её не держит. Заполнен —
   * replace: какой план заменит новый (на особую дату с тремя — последний).
   * over — планов больше лимита дня: заменой одного не уложиться.
   */
  const awayOn = useCallback(
    (
      iso: string | null,
    ): { text: string; blocked: boolean; over: boolean; names: string[]; replace: PlanLocation | null } | null => {
      const item = iso ? elsewhere.get(iso) : undefined;
      if (!item || item.locations.length === 0) {
        return null;
      }
      const names = item.locations.map((location) => location.name);
      const more = names.length > 1 ? ` +${names.length - 1}` : "";
      const blocked = item.full && !planOn(iso);
      const over = blocked && item.limit !== undefined && item.locations.length > item.limit;
      return {
        text: `${runToText(names[0])}${more}`,
        blocked,
        over,
        names,
        replace: blocked && !over ? item.locations[item.locations.length - 1] : null,
      };
    },
    [elsewhere, planOn],
  );

  // Даты этой локации, что уже стоят в постоянном плане здесь.
  const regularDates = useMemo(() => {
    const set = new Set<string>();
    if (!hereSeries || !dates) {
      return set;
    }
    for (const item of dates) {
      if (seriesCoversDate(hereSeries, item.date, { special: item.kind === "special" })) {
        set.add(item.date);
      }
    }
    return set;
  }, [hereSeries, dates]);

  const applyForm = (plan: PlanItem | null) => {
    setFormFrom(plan?.id ?? null);
    setOncePublic(plan?.is_public ?? null);
  };

  // С чего начинать «Публично | Приватно» и действующий постоянный план — один раз на окно.
  useEffect(() => {
    let cancelled = false;
    void Promise.allSettled([getPlannerSettings(), getMySeries()]).then(([settingsResult, seriesResult]) => {
      if (cancelled) {
        return;
      }
      if (settingsResult.status === "fulfilled") {
        setSettings(settingsResult.value);
      } else {
        setSettingsFailed(true);
      }
      setActiveSeries(seriesResult.status === "fulfilled" ? seriesResult.value.series : null);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Особые даты года: от даты — чтобы стрелки шагали и по ним; в обоих входах
  // — особый ли выбранный день (лимит и «оставить постоянный»). Сбой не
  // страшен: стрелки пройдут по субботам, особый старт локации виден и так.
  useEffect(() => {
    let cancelled = false;
    const end = toIsoDay(addDays(parseIsoDay(todayIso), HORIZON_DAYS));
    getSpecialDates(todayIso, end)
      .then((payload) => {
        if (!cancelled) {
          setSpecialDays(payload.items.map((item) => ({ date: item.date, title: item.title })));
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [todayIso]);

  // Календарь и свои планы выбранной локации.
  useEffect(() => {
    if (!placeKey) {
      setDates(null);
      setMine([]);
      setElsewhere(new Map());
      setHereSeries(null);
      setLoadError(null);
      return;
    }
    let cancelled = false;
    setLoadError(null);
    setDates(null);
    setPreview(null);
    void Promise.allSettled([getLocationStartDates(placeKey), getLocationPlans(placeKey)]).then(
      ([datesResult, blockResult]) => {
        if (cancelled) {
          return;
        }
        if (datesResult.status === "rejected") {
          setLoadError(errorText(datesResult.reason, "Не удалось загрузить даты стартов"));
          return;
        }
        // Без своих планов форму не показываем: окно не знало бы, на какие даты
        // здесь уже есть план, и правку приняло бы за новый.
        if (blockResult.status === "rejected") {
          setLoadError(errorText(blockResult.reason, "Не удалось загрузить ваши планы"));
          return;
        }
        // Свои строки планов — без вхождений постоянного: у них нет id.
        const ownPlans = blockResult.value.mine.filter(
          (item) => item.kind === "plan" && !item.past && item.id !== null,
        );
        setMine(ownPlans);
        const away = new Map((blockResult.value.elsewhere ?? []).map((item) => [item.date, item]));
        setElsewhere(away);
        const here = blockResult.value.my_series ?? null;
        setHereSeries(here);
        setOpening(blockResult.value.upcoming ? { url: blockResult.value.official_url ?? null } : null);
        const payload = datesResult.value;
        setDates(payload.dates);
        setPlannable(payload.plannable);
        setReason(payload.reason);
        // Какую дату выбрать. От даты — ту, что в окне (её могли сдвинуть
        // стрелками до выбора локации). От локации — дату плана, который правим,
        // или попрошенную; иначе — ближайший старт без своего плана и без
        // отмены. Даты в календаре нет (старт прошёл, особую дату сняли, у
        // этой локации её нет) — ничего не подставляем: молча подставленная
        // дата превратила бы правку в перенос, а план — в план на другой день.
        const own = new Set(ownPlans.map((item) => item.plan_date));
        const inCalendar = (iso: string | null | undefined) =>
          Boolean(iso && payload.dates.some((item) => item.date === iso));
        const asked = fromDate ? selectedRef.current : existing ? null : (initialDate ?? null);
        const askedMissing = Boolean(asked) && !inCalendar(asked);
        setMissingDate(askedMissing ? asked : null);
        const covered = (iso: string) => {
          const item = payload.dates.find((row) => row.date === iso);
          return Boolean(here && item && seriesCoversDate(here, iso, { special: item.kind === "special" }));
        };
        const initial = fromDate
          ? asked
          : existing
            ? inCalendar(existing.plan_date)
              ? existing.plan_date
              : null
            : asked
              ? askedMissing
                ? null
                : asked
              : (payload.dates.find(
                  (item) =>
                    !item.cancelled && !own.has(item.date) && !covered(item.date) && !away.get(item.date)?.full,
                )?.date ?? null);
        setSelected(initial);
        const initialPlan =
          (initial && ownPlans.find((item) => item.plan_date === initial)) ||
          (existing && existing.plan_date === initial ? existing : null);
        if (initialPlan) {
          setFormFrom(initialPlan.id);
          setOncePublic(initialPlan.is_public ?? null);
        }
        // Выбранная дата дальше первых восьми — раскрываем список, иначе выбор
        // не виден.
        const index = payload.dates.findIndex((item) => item.date === initial);
        if (index >= VISIBLE_DATES && payload.dates[index]?.kind !== "special") {
          setShowAll(true);
        }
      },
    );
    return () => {
      cancelled = true;
    };
    // existing и initialDate читаются один раз при открытии окна, выбранная
    // дата — через selectedRef в момент смены локации.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [placeKey, loadVersion]);

  // «Публично | Приватно». Закрытый профиль — только приватно. Не трогали —
  // как у плана или как профиль. Настройки не загрузились — на экране
  // «Приватно», его и шлём: сохраняется то, что человек видел. Пока они
  // грузятся, переключателя нет, и поле не шлём — сервер возьмёт профиль.
  const forced = settings?.forced ?? null;
  const closedProfile = forced === "profile_private";
  const defaultPublic = settings ? settings.default_public && !closedProfile : settingsFailed ? false : null;
  const seriesMode = mode === "series";
  // Свой разовый план на субботе своего постоянного: для чужих он — суббота
  // постоянного, и публичность у него та же. Переключатель здесь только
  // показывает её и не шлётся — меняют в постоянном плане (сервер другой
  // выбор не примет; ревью 27.10.2026, PRIV-2).
  const seriesOwned = !seriesMode && selected !== null ? (planOn(selected)?.series ? planOn(selected) : null) : null;
  const chosenPublic = seriesMode ? (seriesPublic ?? hereSeries?.is_public ?? null) : oncePublic;
  const publicToSave: boolean | undefined = seriesOwned
    ? undefined
    : closedProfile
      ? false
      : (chosenPublic ?? defaultPublic ?? undefined);
  const formPublic = seriesOwned ? seriesOwned.is_public && !closedProfile : (publicToSave ?? false);

  // Первая дата постоянного плана, которую видит сервер: правка — прежняя
  // первая, пока она впереди (иначе её уже не сдвинуть); новый от даты — день
  // окна, если это суббота (турист выбрал 14 ноября — с него и считаем); новый
  // от локации — ближайшая (null).
  const seriesStart =
    hereSeries !== null
      ? hereSeries.starts_on >= todayIso
        ? hereSeries.starts_on
        : null
      : fromDate && selected && selected >= todayIso && isSaturday(selected)
        ? selected
        : null;
  const previewWanted = seriesMode && placeKey !== null && dates !== null && plannable;

  useEffect(() => {
    if (!previewWanted || !placeKey) {
      return;
    }
    let cancelled = false;
    setPreviewError(null);
    getSeriesPreview({ key: placeKey, starts_on: seriesStart, limit: VISIBLE_DATES })
      .then((payload) => {
        if (cancelled) {
          return;
        }
        setPreview(payload);
        const next: Record<string, boolean> = {};
        for (const item of payload.dates) {
          next[item.date] = item.state === "on";
        }
        setToggles(next);
      })
      .catch((err) => {
        if (!cancelled) {
          setPreview(null);
          setPreviewError(errorText(err, "Не удалось загрузить субботы"));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [previewWanted, placeKey, seriesStart, loadVersion]);

  const selectDate = (iso: string) => {
    setSelected(iso);
    setError(null);
    setMissingDate(null);
    if (existing) {
      // Правка: другая дата — это перенос правимого плана. Даты других своих
      // планов недоступны (DateChip occupied), так что подставлять чужую форму
      // неоткуда.
      return;
    }
    const plan = planOn(iso);
    if (plan) {
      applyForm(plan);
    } else if (formFrom !== null) {
      // Ушли с даты, где форма была заполнена своим планом, — его выбор
      // публичности новой дате не принадлежит.
      applyForm(null);
    }
  };

  const pickPlace = (option: PlannerLocationOption, keyboard: boolean) => {
    setError(null);
    // Выбор своего плана на прежней локации новой не принадлежит.
    if (formFrom !== null) {
      applyForm(null);
    }
    setPlace({ key: option.key, name: option.name, option });
    // Выбрали с клавиатуры — поле исчезло, фокус на «изменить», а не на <body>.
    if (keyboard) {
      requestAnimationFrame(() => changePlaceRef.current?.focus({ preventScroll: true }));
    }
  };

  const changePlace = () => {
    setError(null);
    if (formFrom !== null) {
      applyForm(null);
    }
    setPickerFocus(true);
    setOpening(null);
    setPlace(null);
  };

  /** Правим план, а на дате уже стоит другой свой план этой локации. */
  const occupiedByOther = (iso: string): boolean => {
    if (!existing) {
      return false;
    }
    const other = mineByDate.get(iso);
    return Boolean(other && other.ref !== existing.ref);
  };

  // Правим план, а дату сменили: сохранение перенесёт его на новую дату.
  const moving = Boolean(existing && selected && selected !== existing.plan_date);
  // Какой план убирает «Убрать план»: в правке — всегда тот, ради которого
  // открыли окно, на какую бы дату ни переключились; в новой отметке — свой
  // план на выбранной дате, если он там есть.
  const editing = existing ?? planOn(selected);
  const existingOffCalendar = Boolean(
    existing && dates !== null && !dates.some((item) => item.date === existing.plan_date),
  );

  const finish = () => {
    setStage("done");
    onSavedRef.current(savedRef.current, outcomeRef.current);
  };

  const afterSave = async () => {
    let offer = false;
    try {
      // Только «включить»: при fix_delivery кнопка «Включить» ничего не чинит.
      offer = (await getNotificationNudge()).kind === "enable";
    } catch {
      // не спросили — не страшно, план уже сохранён
    }
    if (offer) {
      setStage("nudge");
    } else {
      finish();
    }
  };

  const handleSave = async () => {
    if (!selected || !placeKey || busy) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const result = await savePlan({
        key: placeKey,
        plan_date: selected,
        ...(publicToSave === undefined ? {} : { is_public: publicToSave }),
        ...(keepOffered && keepSeries ? { keep_series: true } : {}),
        ...(replacing ? { replace_key: replacing.key } : {}),
      });
      // С этого момента план на новой дате есть в базе, что бы ни случилось
      // дальше: закрытие окна обязано перечитать данные родителя.
      savedRef.current = result.plan;
      outcomeRef.current = {
        displaced: result.displaced ?? null,
        seriesSleeping: Boolean(result.series_sleeping),
      };
      if (moving && existing?.id) {
        try {
          await deletePlan(existing.id);
        } catch (err) {
          // 404 — прежнего плана уже нет, перенос и так состоялся.
          if (!(err instanceof ApiError && err.status === 404)) {
            // Перенос — два запроса, а не один: новый план уже сохранён, и
            // «Не удалось сохранить» было бы неправдой. Окно остаётся на форме:
            // «Убрать план» здесь убирает как раз прежний.
            setSaving(false);
            setMoveError(
              `Новый план сохранён, прежний на ${planDayLabel(existing.plan_date)} убрать не удалось — ` +
                "уберите его вручную",
            );
            return;
          }
        }
      }
      // Кнопка крутится, пока спрашиваем про уведомления: окно не мигает
      // пустым экраном между формой и следующим вопросом.
      await afterSave();
      setSaving(false);
    } catch (err) {
      setSaving(false);
      setError(errorText(err, "Не удалось сохранить план"));
    }
  };

  // Срок у постоянного плана из окна не задаётся. Правка не трогает срок,
  // поставленный раньше, — кроме срока, который кончился или вот-вот кончится
  // («Продлить»): кончившийся сервер не примет, а прежний живой ничего бы не
  // продлил. Тогда сохранение — «пока не остановите», и окно так и пишет.
  const seriesUntil = hereSeries && !seriesNeedsRenewal(hereSeries, todayIso) ? hereSeries.until : null;

  const seriesBody = (replace: boolean): SeriesSaveBody => {
    const skip: string[] = [];
    const unskip: string[] = [];
    for (const item of (preview?.dates ?? []).slice(0, VISIBLE_DATES)) {
      const now = toggles[item.date];
      if (item.state === "on" && now === false) skip.push(item.date);
      if (item.state === "skipped" && now === true) unskip.push(item.date);
    }
    return {
      key: placeKey ?? "",
      starts_on: seriesStart,
      until: seriesUntil,
      skip,
      unskip,
      replace,
      ...(publicToSave === undefined ? {} : { is_public: publicToSave }),
    };
  };

  const handleSaveSeries = async (replace = false) => {
    if (busy || !placeKey) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const result = await saveSeries(seriesBody(replace));
      outcomeRef.current = {
        series: result.series,
        seriesCreated: hereSeries === null,
        replaced: result.replaced,
        skippedFailed: result.skipped_failed,
      };
      await afterSave();
      setSaving(false);
    } catch (err) {
      setSaving(false);
      // 409 с вопросом — постоянный план уже на другой локации: «Заменить?».
      // 409 без вопроса — план сохранили в другой вкладке, просто ошибка.
      if (!replace && err instanceof ApiError && err.status === 409 && err.message.includes("?")) {
        setConflict(err.message);
        setStage("conflict");
        return;
      }
      setError(errorText(err, "Не удалось сохранить постоянный план"));
    }
  };

  const handleStop = async () => {
    if (busy) {
      return;
    }
    setStopBusy(true);
    try {
      await stopSeries();
      setStopBusy(false);
      outcomeRef.current = { seriesStopped: hereSeries?.location ?? null };
      finish();
    } catch (err) {
      setStopBusy(false);
      setStage("form");
      setError(errorText(err, "Не удалось остановить постоянный план"));
    }
  };

  const handleDelete = async () => {
    if (!editing?.id || busy) {
      return;
    }
    // Поездка вытеснила субботу постоянного — спросить, вернуть ли её или
    // «Не бегаю» (Q07). Недоделанный перенос убирает прежний план без окна.
    if (editing.displaces && !savedRef.current) {
      setStage("trip");
      return;
    }
    setDeleting(true);
    setError(null);
    try {
      await deletePlan(editing.id);
      setDeleting(false);
      setStage("done");
      // Убрали прежний план после недоделанного переноса — перенос завершён,
      // и родителю нужен новый план, а не «план убран».
      const survivor = savedRef.current && savedRef.current.ref !== editing.ref ? savedRef.current : null;
      onSavedRef.current(survivor, survivor ? outcomeRef.current : { removedDate: editing.plan_date });
    } catch (err) {
      setDeleting(false);
      setError(errorText(err, "Не удалось убрать план"));
    }
  };

  const handleNudgeEnable = async () => {
    setNudgeBusy(true);
    try {
      const result = await enableNotifications();
      if (result.channel) {
        finish();
        return;
      }
      setNudgeNotice("Пока писать некуда: разрешите боту или сообществу отправлять вам сообщения в настройках.");
    } catch (err) {
      setNudgeNotice(errorText(err, "Не удалось включить уведомления"));
    } finally {
      setNudgeBusy(false);
    }
  };

  const handleNudgeLater = useCallback(() => {
    // «Не сейчас» — как у призыва в кабинете: неделю не спрашиваем, иначе
    // окно всплывало бы после каждого плана.
    void snoozeNotificationNudge().catch(() => undefined);
    setStage("done");
    onSavedRef.current(savedRef.current, outcomeRef.current);
  }, []);

  const backToForm = useCallback(() => setStage("form"), []);

  if (stage === "done") {
    return null;
  }

  if (stage === "nudge") {
    return (
      <ConfirmModal
        open
        title="Включить уведомления?"
        confirmLabel="Включить уведомления"
        cancelLabel="Не сейчас"
        confirmLoading={nudgeBusy}
        onConfirm={() => void handleNudgeEnable()}
        onCancel={handleNudgeLater}
      >
        <p>
          Накануне старта напомним о плане, а если старт отменят — сразу сообщим. В Telegram, VK или
          на почту. Сейчас уведомления выключены.
        </p>
        <p className="muted">
          Напоминания не нужны — их можно выключить в «Моих планах», раздел «Уведомления о планах»: об отмене
          старта всё равно сообщим.
        </p>
        {nudgeNotice && <p className="error-text">{nudgeNotice}</p>}
      </ConfirmModal>
    );
  }

  if (stage === "conflict") {
    return (
      <ConfirmModal
        open
        title="Заменить постоянный план?"
        confirmLabel="Заменить"
        cancelLabel="Оставить прежний"
        confirmLoading={saving}
        hideFromWebvisor
        onConfirm={() => void handleSaveSeries(true)}
        onCancel={backToForm}
      >
        <p>{conflict}</p>
        {error && <p className="error-text">{error}</p>}
      </ConfirmModal>
    );
  }

  if (stage === "stop" && hereSeries) {
    return (
      <ConfirmModal
        open
        title="Остановить постоянный план?"
        confirmLabel="Остановить"
        cancelLabel="Оставить"
        variant="danger"
        confirmLoading={stopBusy}
        hideFromWebvisor
        onConfirm={() => void handleStop()}
        onCancel={backToForm}
      >
        {hereSeries.location.name}: будущие субботы уйдут из плана, прошлые останутся в истории. Отметки «Не
        бегаю» останутся.
      </ConfirmModal>
    );
  }

  if (stage === "trip" && editing) {
    return (
      <TripRemoveModal
        plan={editing}
        onCancel={backToForm}
        onDone={({ rest }) => {
          setStage("done");
          onSavedRef.current(null, {
            rested: rest,
            returned: rest ? null : (editing.displaces?.location ?? null),
            removedDate: editing.plan_date,
          });
        }}
      />
    );
  }

  const visibleDates =
    dates === null
      ? []
      : showAll
        ? dates
        : dates.filter((item, index) => index < VISIBLE_DATES || item.kind === "special" || item.date === selected);
  const hiddenCount = dates === null ? 0 : dates.length - visibleDates.length;
  const selectedItem = dates?.find((item) => item.date === selected) ?? null;
  const ready = dates !== null && plannable && dates.length > 0;
  // Постоянный план на другой локации — подсказки о поездке и о замене.
  const otherSeries =
    activeSeries && !hereSeries && activeSeries.location.key !== placeKey ? activeSeries : null;
  // Выбранная дата уже в постоянном плане здесь, а своего разового плана на
  // ней нет: ставить нечего — суббота и так ваша.
  const selectedRegular = Boolean(selected && regularDates.has(selected) && !planOn(selected));
  // В выбранный день стоит суббота постоянного плана в другой локации (разовых
  // планов в других локациях нет — иначе они её уже вытеснили).
  const coversElsewhere =
    !seriesMode && otherSeries && selected && selectedItem && !awayOn(selected)
      ? seriesCoversDate(otherSeries, selected, { special: selectedItem.kind === "special" })
      : false;
  // Особый день — по самой дате, как лимит у сервера: особый старт локации или
  // дата из списка особых.
  const specialSelected = Boolean(
    selected && (selectedItem?.kind === "special" || specialDays.some((item) => item.date === selected)),
  );
  // На особую дату постоянный план можно оставить рядом с новой локацией.
  const keepOffered = Boolean(coversElsewhere && !existing && specialSelected);
  // Разовый план в другой локации на дату постоянного — та суббота уйдёт.
  const displacesHint = coversElsewhere && !existing?.displaces && !(keepOffered && keepSeries);
  const seriesEditing = seriesMode && hereSeries !== null;
  const seriesReady = seriesMode && preview !== null;
  // День уже заполнен планом в другой локации — новый план заменит его.
  const selectedAway = awayOn(selected);
  const replacing = !seriesMode ? (selectedAway?.replace ?? null) : null;
  // Планов на день больше лимита — сервер не примет ни новый, ни замену.
  const overLimit = !seriesMode && Boolean(selectedAway?.over);
  // От даты дата может не совпасть с календарём локации — тогда ставить нечего.
  const canSave =
    ready && (seriesMode ? seriesReady : !selectedRegular && !overLimit && selectedItem !== null);
  const onceLabel = replacing ? "Заменить план" : moving ? "Перенести план" : editing ? "Сохранить" : "Планирую";
  const saveLabel = seriesMode ? (seriesEditing ? "Сохранить" : "Планирую каждую субботу") : onceLabel;
  // Переключать вид окна можно, пока не правим разовый план. От даты —
  // переключатель стоит рядом с датой и до выбора локации; прячется, только
  // если выбранную локацию нельзя запланировать.
  const seriesAllowed = !existing && (fromDate ? plannable || dates === null : ready);
  // От даты, пока локация не выбрана: кнопка видна, но не нажимается —
  // понятно, что дальше.
  const waitingPlace = fromDate && place === null;

  // Кнопки — в подвале окна, а не в конце формы: на телефоне полсотни дат
  // уводят их за экран, и «Планирую» приходилось искать прокруткой.
  // Ошибки — там же: их вызывают эти кнопки, и прокрученное тело их прятало бы.
  const showRemove = !seriesMode && editing;
  const showStop = seriesEditing;
  const showPrimary = ready || waitingPlace;
  const hasFooter = Boolean(showRemove || showStop || showPrimary || error || moveError);
  const footer = hasFooter ? (
    <div className="plan-modal-footer">
      {moveError && (
        <p className="error-text" role="alert">
          {moveError}
        </p>
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {/* По числу отметок организаторы готовятся к старту (решение Дмитрия
          27.10.2026): отметка «на всякий случай» им вредит. */}
      {showPrimary && (
        <p className="plan-modal-footnote">
          Отметка ни к чему не обязывает, но не ставьте её «на всякий случай»: по числу отметок организаторы готовятся
          к старту. {seriesMode ? "Не придёте — снимите субботу." : "Передумали — уберите план."}
        </p>
      )}
      {(showRemove || showStop || showPrimary) && (
        <div className="plan-modal-actions">
          {showRemove && (
            <button type="button" className="btn secondary" disabled={busy} onClick={() => void handleDelete()}>
              {deleting && <span className="btn-spinner" aria-hidden="true" />}
              {deleting ? "Убираем…" : "Убрать план"}
            </button>
          )}
          {/* «Остановить» — заметно, красным (отзыв Дмитрия 27.10.2026: «не
              нашёл, как снять постоянную пробежку»). */}
          {showStop && (
            <button type="button" className="btn btn-ghost btn-danger" disabled={busy} onClick={() => setStage("stop")}>
              Остановить план
            </button>
          )}
          {showPrimary && (
            <button
              type="button"
              className="btn primary"
              disabled={busy || !canSave || (!seriesMode && !selected)}
              onClick={() => void (seriesMode ? handleSaveSeries() : handleSave())}
            >
              {saving && <span className="btn-spinner" aria-hidden="true" />}
              {saving ? "Сохраняем…" : saveLabel}
            </button>
          )}
        </div>
      )}
    </div>
  ) : undefined;

  // «Один раз | ↻ Каждую субботу» — на уровне даты (решение Дмитрия 27.10.2026).
  const segment = seriesAllowed ? (
    <div className="plan-mode-seg" role="group" aria-label="Как планировать">
      <button type="button" aria-pressed={!seriesMode} disabled={busy} onClick={() => setMode("once")}>
        Один раз
      </button>
      <button type="button" aria-pressed={seriesMode} disabled={busy} onClick={() => setMode("series")}>
        {SERIES_ICON} Каждую субботу
      </button>
    </div>
  ) : null;

  // Приватные разовые планы здесь в даты постоянного станут его субботами, а
  // публичность у постоянного одна на все: сохранение «Публично» откроет и
  // их (ревью 27.10.2026, PRIV-6) — говорим заранее.
  const seriesFrom = seriesStart ?? todayIso;
  const pulledPrivate =
    seriesMode && formPublic && !closedProfile
      ? mine
          .filter(
            (item) =>
              !item.series &&
              !item.is_public &&
              item.plan_date >= seriesFrom &&
              (!seriesUntil || item.plan_date <= seriesUntil),
          )
          .map((item) => item.plan_date)
      : [];
  const pulledPrivateHint =
    pulledPrivate.length > 0 ? (
      <p className="plan-modal-hint">
        {pulledPrivate.length === 1
          ? `План здесь на ${planDayLabel(pulledPrivate[0])} приватный — станет публичным:`
          : `Планы здесь на ${shortDays(pulledPrivate)} приватные — станут публичными:`}{" "}
        у постоянного плана публичность одна на все субботы.
      </p>
    ) : null;

  // Постоянный план бывает один: на другой локации сервер спросит «Заменить?».
  const otherSeriesHint =
    seriesMode && otherSeries ? (
      <p className="plan-modal-hint">
        Постоянный план бывает один, сейчас он — {otherSeries.location.name}. Сохраните — спросим, заменить ли.
      </p>
    ) : null;

  // Подсказки о выбранной дате — под чипами (от локации) или под строкой
  // локации (от даты).
  const dateHints = (
    <>
      {overLimit && selected && selectedAway && (
        <p className="plan-modal-hint plan-modal-hint-warn" role="status">
          На {planDayLabel(selected)} у вас планов больше, чем можно: {selectedAway.names.join(", ")}. Уберите
          лишние в «Моих планах».
        </p>
      )}
      {replacing && selected && selectedAway && (
        <p className="plan-modal-hint plan-modal-hint-warn" role="status">
          {selectedAway.names.length > 1
            ? `На ${planDayLabel(selected)} у вас уже ${selectedAway.names.length} плана — последний из них (${replacing.name}) будет отменён.`
            : `План на ${planDayLabel(selected)} (${replacing.name}) будет отменён.`}
        </p>
      )}
      {opening && (
        <p className="plan-modal-hint plan-modal-hint-warn">
          Локация ещё не открылась. Дату первого старта проверьте{" "}
          {opening.url ? (
            <a href={opening.url} target="_blank" rel="noreferrer">
              на сайте системы
            </a>
          ) : (
            "на сайте системы"
          )}
          : на открытие отмечаются заранее, а старт может сдвинуться.
        </p>
      )}
      {selectedItem?.cancelled && (
        <p className="plan-modal-hint plan-modal-hint-warn">
          Старт отменён{selectedItem.cancel_reason ? `: ${selectedItem.cancel_reason}` : ""}
        </p>
      )}
      {selectedRegular && !seriesMode && (
        <p className="plan-modal-hint">
          Эта дата уже в вашем постоянном плане — отмечать её не нужно.
        </p>
      )}
      {keepOffered && otherSeries && (
        <label className="plan-keep-series">
          <input
            type="checkbox"
            checked={keepSeries}
            disabled={busy}
            onChange={(event) => setKeepSeries(event.target.checked)}
          />
          <span>
            Оставить и постоянный план — {otherSeries.location.name}: в этот день можно до трёх стартов
          </span>
        </label>
      )}
      {displacesHint && otherSeries && selected && (
        <p className="plan-modal-hint">
          Постоянный план — {otherSeries.location.name} — на {planDayLabel(selected)} не считаем: вернётся, если уберёте
          этот план.
        </p>
      )}
      {moving && existing && !moveError && (
        <p className="plan-modal-hint">План с {planDayLabel(existing.plan_date)} перенесём на эту дату.</p>
      )}
      {existingOffCalendar && !selected && existing && (
        <p className="plan-modal-hint">
          Старта {planDayLabel(existing.plan_date)} в календаре локации уже нет — выберите другую дату или уберите
          план.
        </p>
      )}
    </>
  );

  const dateChips = (
    <div className="plan-date-chips" role="group" aria-labelledby="plan-date-label">
      {visibleDates.map((item) => (
        <DateChip
          key={item.date}
          item={item}
          selected={item.date === selected}
          mine={mineByDate.has(item.date)}
          regular={!seriesMode && regularDates.has(item.date)}
          occupied={occupiedByOther(item.date)}
          away={awayOn(item.date)}
          onSelect={() => selectDate(item.date)}
        />
      ))}
    </div>
  );

  // Локация окна от локации — заданная, без «изменить»: окно открыли с её страницы.
  const fixedPlace = (
    <p className="plan-modal-place">
      <span aria-hidden="true">📍 </span>
      {place?.name}
    </p>
  );

  // Ждём, с чего начинать (настройки), чтобы переключатель не прыгал с
  // «Приватно» на «Публично».
  const visibility =
    settings || settingsFailed ? (
      <>
        <PlanVisibilityToggle
          value={formPublic}
          forced={forced}
          disabled={busy || seriesOwned !== null}
          onChange={(next) => (seriesMode ? setSeriesPublic(next) : setOncePublic(next))}
        />
        {seriesOwned && !closedProfile && (
          <p className="plan-modal-hint">Как у постоянного плана — публичность меняется в нём.</p>
        )}
      </>
    ) : null;

  const previewState = previewError ? (
    <div className="plan-modal-load-error">
      <p className="error-text">{previewError}</p>
      <button type="button" className="btn secondary btn-sm" onClick={() => setLoadVersion((v) => v + 1)}>
        Повторить
      </button>
    </div>
  ) : !preview ? (
    <p className="muted">Считаем субботы…</p>
  ) : null;

  // ---------- дата, локация, публичность ----------

  let head: ReactNode;
  // Локация окна от локации — после дат: порядок окна один для обоих входов.
  let placeAfterDates: ReactNode = null;
  if (fromDate) {
    // Стрелки: с выбранной локацией — по её календарю (отменённые пропускаем),
    // без неё — по субботам и особым датам года.
    const stepList =
      place && dates
        ? dates.filter((item) => !item.cancelled || item.date === selected).map((item) => item.date)
        : startDays(todayIso, specialDays.map((item) => item.date));
    const current = selected ?? initialDate ?? todayIso;
    const prev = [...stepList].reverse().find((iso) => iso < current) ?? null;
    const next = stepList.find((iso) => iso > current) ?? null;
    const title =
      selectedItem?.kind === "special"
        ? selectedItem.title
        : (specialDays.find((item) => item.date === current)?.title ?? null);
    const option = place?.option;
    const chipMeta = [
      option?.city && option.city !== option.name ? option.city : null,
      option?.platforms.length ? option.platforms.map((code) => platformCodeLabel(code)).join(", ") : null,
      ...(selectedItem ? startFacts(selectedItem) : []),
    ].filter(Boolean);
    head = (
      <>
        <div className="plan-modal-when">
          <DateStepper
            iso={current}
            title={title}
            prev={prev}
            next={next}
            onStep={(iso) => (place ? selectDate(iso) : setSelected(iso))}
          />
          {segment}
        </div>
        {place ? (
          <div className="plan-place-chip">
            <span className="plan-place-chip-icon" aria-hidden="true">
              📍
            </span>
            <span className="plan-place-chip-body">
              <strong className="plan-place-chip-name">{place.name}</strong>
              {chipMeta.length > 0 && <span className="plan-place-chip-meta">{chipMeta.join(" · ")}</span>}
            </span>
            <button
              ref={changePlaceRef}
              type="button"
              className="link-button plan-place-chip-change"
              disabled={busy}
              onClick={changePlace}
            >
              изменить
            </button>
          </div>
        ) : (
          <PlanPlacePicker date={current} autoFocus={pickerFocus} onPick={pickPlace} />
        )}
      </>
    );
  } else if (ready) {
    // От локации: «Когда» и переключатель одной строкой, ниже — чипы дат или
    // субботы постоянного плана, потом локация.
    head = (
      <div className="plan-modal-when">
        <span className="plan-modal-label" id="plan-date-label">
          Когда
        </span>
        {segment}
      </div>
    );
    placeAfterDates = fixedPlace;
  } else {
    // Даты ещё грузятся или локацию нельзя запланировать — переключать нечего,
    // а название локации объясняет, о чём окно.
    head = fixedPlace;
  }

  let body: ReactNode = null;
  if (ready) {
    if (!seriesMode) {
      body = (
        <>
          {fromDate ? (
            <>
              {missingDate && (
                <p className="plan-modal-hint plan-modal-hint-warn">
                  На {planDayLabel(missingDate)} здесь старта нет — выберите другую дату стрелками.
                </p>
              )}
              {dateHints}
            </>
          ) : (
            <div className="plan-modal-field">
              {/* Над датами, а не под ними: на телефоне под полсотней чипов
                  строку никто бы не увидел. */}
              {missingDate && !selected && (
                <p className="plan-modal-hint">На {planDayLabel(missingDate)} здесь старта нет — выберите другую дату.</p>
              )}
              {dateChips}
              {hiddenCount > 0 && dates && (
                <button type="button" className="link-button plan-dates-more" onClick={() => setShowAll(true)}>
                  Показать все даты на год ({dates.length})
                </button>
              )}
              {dateHints}
            </div>
          )}
          {placeAfterDates}
          {visibility}
        </>
      );
    } else {
      const seriesControls = (
        <>
          {placeAfterDates}
          {visibility}
          {pulledPrivateHint}
        </>
      );
      body = (
        <>
          {hereSeries && (
            <p className="plan-series-lead">
              <strong>
                {SERIES_ICON} Ваш постоянный план {seriesSpanText(hereSeries)}
              </strong>
            </p>
          )}
          {previewState}
          {/* Локация и публичность — сразу под субботами, до абзаца о паузе. */}
          {preview ? (
            <SeriesOptions
              preview={preview}
              until={seriesUntil}
              editing={hereSeries !== null}
              toggles={toggles}
              visible={VISIBLE_DATES}
              onToggle={(iso) => setToggles((prev) => ({ ...prev, [iso]: !prev[iso] }))}
              wakesByPresence={hereSeries?.wakes_by_presence ?? preview.wakes_by_presence ?? true}
              named={formPublic && forced !== "minor"}
              afterDates={seriesControls}
            />
          ) : (
            seriesControls
          )}
          {otherSeriesHint}
        </>
      );
    }
  }

  return (
    <DetailModal
      open
      title={seriesMode ? "Планирую каждую субботу" : "Планирую пробежать"}
      width="narrow"
      onClose={requestClose}
      footer={footer}
      panelRef={panelRef}
      hideFromWebvisor
    >
      <div className="plan-modal plan-modal-form">
        {head}

        {loadError && (
          <div className="plan-modal-load-error">
            <p className="error-text">{loadError}</p>
            <button type="button" className="btn secondary btn-sm" onClick={() => setLoadVersion((v) => v + 1)}>
              Повторить
            </button>
          </div>
        )}

        {placeKey && !loadError && dates === null && <p className="muted">Загружаем даты стартов…</p>}

        {dates !== null && !plannable && (
          <p className="muted">{reason || "Эту локацию пока нельзя запланировать."}</p>
        )}

        {dates !== null && plannable && dates.length === 0 && (
          <p className="muted">Ближайший год стартов в календаре нет.</p>
        )}

        {body}
      </div>
    </DetailModal>
  );
}
