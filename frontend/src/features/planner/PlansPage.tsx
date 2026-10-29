import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ConfirmModal } from "../../components/ConfirmModal";
import { LocationNameLink } from "../../components/LocationNameLink";
import { PlatformBadge } from "../../components/PlatformBadge";
import { Snackbar } from "../../components/Snackbar";
import { useCachedResource } from "../../hooks/useCachedResource";
import { useRestorableState } from "../../hooks/useRestorableState";
import { useSnackbar } from "../../hooks/useSnackbar";
import { dropCached } from "../../lib/dataCache";
import { formatDuration, formatInt, platformCodeLabel } from "../../lib/format";
import { PORTAL_CABINET_PLANS_HREF } from "../../lib/portalRoutes";
import { useDayRest, useRestFocus, type DayRestControls } from "./DayRest";
import { moscowTodayIso, relativeDayLabel } from "./NextPlanCard";
import { PlanModal } from "./PlanModal";
import {
  deletePlan,
  getMyPlans,
  getMySeries,
  getPlannerSettings,
  getSpecialDates,
  isSeriesOccurrence,
  resumeSeries,
  saveDayRest,
  stopSeries,
  type PlanItem,
  type PlannerSettings,
  type PlanSeries,
  type SpecialDateInfo,
} from "./plannerApi";
import { SeriesCard } from "./SeriesCard";
import {
  errorText,
  planOutcomeSnackbar,
  SERIES_ICON,
  SeriesDayRemoveModal,
  SeriesHintCard,
  TripRemoveModal,
  type PlanModalOutcome,
} from "./seriesParts";
import { DayDetailsHead, dayTitle, MonthCalendar, type CalendarDayView } from "./MonthCalendar";
import { addDays, formatWeekdayDayMonth, parseIsoDay, planDayLabel, toIsoDay, weekdayMonFirst } from "./plannerDates";
import { plansHashCommand, type PlansHashCommand } from "./plannerLinks";
import { PlanNotificationsSection } from "./PlanNotificationsSection";
import { PlanVisibilityToggle } from "./PlanVisibility";
import "./plansPage.css";

// «Не бегаю» и «Планирую» — на дни стартов от сегодня до горизонта календаря.
const PLAN_FUTURE_DAYS = 365;
// На обычную субботу — один план, на особую дату — до трёх (лимит сервера).
const SPECIAL_DAY_PLANS = 3;
// «Ближайшие» на телефоне: сервер отдаёт не больше 120 дней за раз.
const UPCOMING_DAYS = 119;
const UPCOMING_PREVIEW = 5;
const PHONE_QUERY = "(max-width: 640px)";

function usePhoneViewport(): boolean {
  const [phone, setPhone] = useState(
    () => typeof window !== "undefined" && window.matchMedia(PHONE_QUERY).matches,
  );
  useEffect(() => {
    const mql = window.matchMedia(PHONE_QUERY);
    const onChange = (event: MediaQueryListEvent) => setPhone(event.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);
  return phone;
}

// ---------- подписи плана ----------

function isDone(plan: PlanItem): boolean {
  return plan.fact?.status === "ran" || plan.fact?.status === "volunteered";
}

/** «пробежали» / «были волонтёром» / «не получилось» — для экранного диктора. */
function factWord(plan: PlanItem): string | null {
  switch (plan.fact?.status) {
    case "ran":
      return "пробежали";
    case "volunteered":
      return "были волонтёром";
    case "missed":
      return "не получилось";
    default:
      return null;
  }
}

/** Прошедший день с «Не бегаю» всё-таки с пробежкой — тогда это ✓, а не отметка. */
function restDayRan(day: PlanItem | null): boolean {
  return Boolean(day?.rest && day.fact?.status === "ran");
}

/** «Пробежали · 23:45 · №214» — время и номер из протокола. */
function ranText(fact: NonNullable<PlanItem["fact"]>): string {
  const time = fact.finish_time_sec != null ? ` · ${formatDuration(fact.finish_time_sec).replace(/^00:/, "")}` : "";
  const number = fact.event_number != null ? ` · №${formatInt(fact.event_number)}` : "";
  return `Пробежали${time}${number}`;
}

/** Подпись дня для экранного диктора: что в нём запланировано. */
function dayAriaLabel(
  iso: string,
  todayIso: string,
  plans: PlanItem[],
  day: PlanItem | null,
  specialTitle: string | null,
): string {
  const base =
    `${formatWeekdayDayMonth(iso)}${iso === todayIso ? ", сегодня" : ""}` +
    (specialTitle ? `, ${specialTitle}` : "");
  const parts = plans.map((plan) => {
    const what =
      `${plan.location?.name ?? "локация"}${isSeriesOccurrence(plan) ? ", постоянный план" : ""}` +
      (!plan.past && plan.is_public === false ? ", приватно" : "");
    const fact = plan.fact?.status === "elsewhere" ? "бегали в другой локации" : factWord(plan);
    return fact && !(isSeriesOccurrence(plan) && plan.fact?.status === "missed") ? `${what}, ${fact}` : what;
  });
  if (day?.rest) {
    parts.push(
      iso >= todayIso
        ? `не бегаю${day.is_public === false ? ", приватно" : ""}`
        : restDayRan(day)
          ? "пробежали"
          : "не бегали",
    );
  }
  return parts.length > 0 ? `${base}: ${parts.join("; ")}` : base;
}

// ---------- детали дня ----------

/** Суббота — день старта; особые даты сервер присылает отдельно. */
function isSaturdayIso(iso: string): boolean {
  return weekdayMonFirst(parseIsoDay(iso)) === 5;
}

/** Публичность отметки «Не бегаю» из деталей дня: тот же PUT, что ставит отметку. */
type RestVisibility = {
  forced: PlannerSettings["forced"];
  busy: boolean;
  error: string | null;
  set: (iso: string, next: boolean) => void;
};

function useRestVisibility(forced: PlannerSettings["forced"], onChanged: () => void): RestVisibility {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Повтор, пока идёт запрос, глушим здесь, а не disabled на кнопке: нажатая
  // кнопка в фокусе, ставшая disabled, роняет фокус на <body> (ревью
  // 27.10.2026, UI-6).
  const busyRef = useRef(false);
  const set = (iso: string, next: boolean) => {
    if (busyRef.current) {
      return;
    }
    busyRef.current = true;
    setBusy(true);
    setError(null);
    saveDayRest(iso, false, next)
      .then(() => onChanged())
      .catch((err) => setError(errorText(err, "Не удалось сохранить отметку")))
      .finally(() => {
        busyRef.current = false;
        setBusy(false);
      });
  };
  return { forced, busy, error, set };
}

/**
 * Замок у приватного плана в клетке и в легенде (правка Дмитрия 27.10.2026:
 * «помечать приватный или публичный план на указанный день»). Тихий контур
 * цветом подписи, а не эмодзи: на 360 он стоит в правом верхнем углу клетки,
 * напротив «↻», и не должен перекрикивать название.
 */
function LockIcon() {
  return (
    <svg className="plans-lock-icon" viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">
      <rect x="2.25" y="5.25" width="7.5" height="5.5" rx="1.2" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M4 5.25V3.9a2 2 0 0 1 4 0v1.35" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}

/** Карандаш и корзина — на телефоне вместо подписей «Изменить» и «Убрать». */
function EditIcon() {
  return (
    <svg className="pday-act-icon" viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
      <path
        d="M13.6 3.3a1.8 1.8 0 0 1 2.6 0l.5.5a1.8 1.8 0 0 1 0 2.6l-9 9-3.6 1 1-3.6 8.5-9.5Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function RemoveIcon() {
  return (
    <svg className="pday-act-icon" viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
      <path
        d="M4 6h12M8 6V4.5h4V6M6 6l.7 9.5h6.6L14 6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * Что под названием плана мелкой строкой: ≈№, отмена старта, вытесненный
 * постоянный план, факт прошедшего. Пусто — строки нет.
 */
function planMeta(plan: PlanItem): ReactNode[] {
  const parts: ReactNode[] = [];
  const occurrence = isSeriesOccurrence(plan);
  if (!plan.past) {
    if (plan.start?.number_forecast != null) {
      parts.push(`≈№${formatInt(plan.start.number_forecast)}`);
    }
    if (plan.start?.cancelled) {
      parts.push(
        <span key="cancel" className="pday-warn">
          старт отменён{plan.start.cancel_reason ? `: ${plan.start.cancel_reason}` : ""}
        </span>,
      );
    }
    // Даты уже нет в календаре локации: админ убрал особую дату, локация ушла
    // на паузу. План остаётся, но напоминания сервер не пришлёт.
    if (!plan.start && plan.location && !occurrence) {
      parts.push(
        <span key="off" className="pday-warn">
          старта нет в календаре локации — напоминания не будет
        </span>,
      );
    }
    if (plan.displaces) {
      parts.push(`${SERIES_ICON} ${plan.displaces.location.name} в этот день не считаем`);
    }
    return parts;
  }
  const fact = plan.fact;
  if (!fact) {
    return parts;
  }
  if (fact.status === "ran") {
    parts.push(
      <span key="fact" className="pday-done">
        ✓ {ranText(fact)}
      </span>,
    );
  } else if (fact.status === "volunteered") {
    // Собирался бежать, а в протоколе — волонтёр этой локации: на старте был.
    const number = fact.event_number != null ? ` · №${formatInt(fact.event_number)}` : "";
    parts.push(
      <span key="fact" className="pday-done">
        ✓ Были волонтёром{number}
      </span>,
    );
  } else if (fact.status === "elsewhere") {
    // Постоянный план, а пробежал в другом месте — без упрёка, просто где.
    parts.push(
      <span key="fact">
        Бегали в другой локации
        {fact.location && (
          <>
            {": "}
            <LocationNameLink name={fact.location.name} slug={fact.location.slug} />
          </>
        )}
      </span>,
    );
  } else if (fact.status === "missed" && !occurrence) {
    // Тихо и серым: план ни к чему не обязывал. У постоянного серых отметок нет.
    parts.push("не получилось");
  } else if (fact.status !== "missed") {
    parts.push("протокола пока нет");
  }
  return parts;
}

/**
 * Строка плана в деталях дня: локация-ссылка, значок системы, тихие пометки
 * «↻ постоянный» и «приватно»; справа — «Изменить» и «Убрать» (на телефоне —
 * значками). У прошедшего действий нет — только факт.
 */
function DayPlanRow({
  plan,
  onEdit,
  onRemove,
}: {
  plan: PlanItem;
  onEdit: (plan: PlanItem) => void;
  onRemove: (plan: PlanItem) => void;
}) {
  const regular = isSeriesOccurrence(plan) || Boolean(plan.series);
  const name = plan.location?.name ?? "Локация";
  const meta = planMeta(plan);
  return (
    <li className={`pday-row${regular ? " pday-row-series" : ""}`}>
      <div className="pday-row-main">
        <p className="pday-row-head">
          <span className="pday-row-name">
            {plan.location ? <LocationNameLink name={plan.location.name} slug={plan.location.slug} /> : name}
          </span>
          {plan.location?.platform_code && <PlatformBadge code={plan.location.platform_code} />}
          {regular && <span className="pday-tag pday-tag-series">{SERIES_ICON} постоянный</span>}
          {!plan.past && plan.is_public === false && <span className="pday-tag">приватно</span>}
        </p>
        {meta.length > 0 && (
          <p className="pday-row-meta">
            {meta.map((part, index) => (
              <Fragment key={index}>
                {index > 0 && " · "}
                {part}
              </Fragment>
            ))}
          </p>
        )}
      </div>
      {!plan.past && (
        <div className="pday-row-actions">
          <button
            type="button"
            className="pday-act"
            aria-label={`Изменить: ${name}`}
            title="Изменить"
            onClick={() => onEdit(plan)}
          >
            <EditIcon />
            <span className="pday-act-text">Изменить</span>
          </button>
          <button
            type="button"
            className="pday-act pday-act-remove"
            aria-label={`Убрать: ${name}`}
            title="Убрать"
            onClick={() => onRemove(plan)}
          >
            <RemoveIcon />
            <span className="pday-act-text">Убрать</span>
          </button>
        </div>
      )}
    </li>
  );
}

/**
 * Детали выбранного дня — компактная панель (отзыв Дмитрия 27.10.2026: «очень
 * колхозно: локация, отдельным блоком «Не бегаю», ниже криво-косо заметки»).
 * Сверху дата; ниже планы дня строками; внизу одна строка действий дня:
 * «Планирую пробежать» (на особую дату — «+ ещё локация», пока не набралось
 * три; в субботу постоянного — «Другая локация») и «Не бегаю». Отмечено «Не
 * бегаю» — строка «Не бегаете · Публично | Приватно · Отменить» и рядом
 * «Планирую пробежать»: передумал — план ставится сразу и снимает отметку
 * сам (правка Дмитрия 27.10.2026).
 *
 * Лимит дня — то же правило, что у окна от локации и сервера: на обычную
 * субботу один план, на особую дату — до трёх; суббота постоянного в счёт
 * идёт (на особую дату её закрепит «+ ещё локация»). Заполненный день —
 * «Другая локация»: новый план заменит прежний, окно об этом предупредит.
 */
function DayPanel({
  iso,
  todayIso,
  plans,
  day,
  specialDate,
  rest,
  visibility,
  onPlan,
  onEdit,
  onRemove,
  onClear,
}: {
  iso: string;
  todayIso: string;
  plans: PlanItem[];
  /** Строка дня без локации — отметка «Не бегаю». */
  day: PlanItem | null;
  /** Особая дата из админки — известна и без плана на неё. */
  specialDate: SpecialDateInfo | null;
  rest: DayRestControls;
  /** «Публично | Приватно» у отметки «Не бегаю». */
  visibility: RestVisibility;
  /** Окно плана от даты; keepSeries — «+ ещё локация» рядом с постоянным. */
  onPlan: (iso: string) => void;
  onEdit: (plan: PlanItem) => void;
  onRemove: (plan: PlanItem, dayRows: PlanItem[]) => void;
  onClear: () => void;
}) {
  const special =
    specialDate?.title ?? plans.find((plan) => plan.start?.kind === "special")?.start?.title ?? null;
  const today = parseIsoDay(todayIso);
  const future = iso >= todayIso;
  const specialDay = specialDate !== null || plans.some((plan) => plan.start?.kind === "special");
  // День старта — суббота или особая дата: на него ставят план и «Не бегаю».
  const startDay = specialDay || isSaturdayIso(iso);
  const inHorizon = future && iso <= toIsoDay(addDays(today, PLAN_FUTURE_DAYS));
  const rested = Boolean(day?.rest);
  const rows = plans.filter((plan) => plan.id !== null);
  const occurrence = plans.find((plan) => isSeriesOccurrence(plan) && !plan.past) ?? null;
  const taken = rows.length + (occurrence ? 1 : 0);
  const limit = specialDay ? SPECIAL_DAY_PLANS : 1;
  // «Rest» ставит фокус обратно на кнопку дня, что пришла взамен нажатой.
  const focusRef = useRestFocus(rest, iso, rested);
  // День заполнен — «Другая локация»: окно от даты разрешит выбрать другую и
  // скажет, какой план будет отменён (правка Дмитрия 27.10.2026).
  const addLabel = taken === 0 ? "Планирую пробежать" : taken < limit ? "+ ещё локация" : "Другая локация";
  const platforms =
    specialDate && future && specialDate.platforms.length > 0
      ? specialDate.platforms.map((code) => platformCodeLabel(code)).join(", ")
      : null;
  return (
    <div className="pday">
      <DayDetailsHead iso={iso} onClear={onClear}>
        {dayTitle(iso)}
        {future && <span className="plans-details-when"> · {relativeDayLabel(iso, todayIso).toLowerCase()}</span>}
      </DayDetailsHead>
      {/* Особую дату админ отмечает для системы целиком, а выходит на старт
          не каждая её локация — без оговорки читалось бы как «бегут везде». */}
      {special && (
        <p className="pday-special">
          <span className="pday-special-title">{special}</span>
          {platforms && <span className="pday-special-platforms"> · старты: {platforms} — не на всех локациях</span>}
        </p>
      )}

      {plans.length > 0 && (
        <ul className="pday-rows">
          {plans.map((plan) => (
            <DayPlanRow key={plan.ref} plan={plan} onEdit={onEdit} onRemove={(item) => onRemove(item, rows)} />
          ))}
        </ul>
      )}

      {startDay && inHorizon ? (
        rested ? (
          <div className="pday-rest">
            <div className="pday-rest-line">
              <span className="pday-rest-title">Не бегаете</span>
              <PlanVisibilityToggle
                value={Boolean(day?.is_public)}
                forced={visibility.forced}
                disabled={rest.busy}
                what="rest"
                compact
                onChange={(next) => visibility.set(iso, next)}
              />
              <button
                ref={focusRef}
                type="button"
                className="link-button pday-rest-undo"
                disabled={rest.busy}
                onClick={() => rest.clearRest(iso)}
              >
                Отменить
              </button>
            </div>
            {/* Передумали — сразу к плану, без «Отменить» сначала (правка
                Дмитрия 27.10.2026): сохранённый план снимает «Не бегаю» сам. */}
            <button type="button" className="btn primary btn-sm pday-rest-plan" onClick={() => onPlan(iso)}>
              Планирую пробежать
            </button>
          </div>
        ) : (
          <div className="pday-actions">
            <button type="button" className="btn primary btn-sm" onClick={() => onPlan(iso)}>
              {addLabel}
            </button>
            <button
              ref={focusRef}
              type="button"
              className="btn secondary btn-sm"
              disabled={rest.busy}
              onClick={() => rest.markRest(iso, rows)}
            >
              {rest.busy && <span className="btn-spinner" aria-hidden="true" />}
              Не бегаю
            </button>
          </div>
        )
      ) : rested && !future ? (
        restDayRan(day) && day?.fact ? (
          <p className="pday-rest-past pday-done">✓ {ranText(day.fact)}</p>
        ) : (
          // Без оценок: отметка — личный планер, не обещание.
          <p className="pday-rest-past">Не бегали</p>
        )
      ) : plans.length > 0 ? null : !future ? (
        <p className="pday-empty">В этот день планов не было.</p>
      ) : startDay ? (
        <p className="pday-empty">Планы ставятся не дальше чем на год вперёд.</p>
      ) : (
        <p className="pday-empty">В этот день стартов нет.</p>
      )}
      {(rest.error || visibility.error) && (
        <p className="error-text" role="alert">
          {rest.error ?? visibility.error}
        </p>
      )}
    </div>
  );
}

// ---------- «Ближайшие» на телефоне ----------

function UpcomingPlans({
  todayIso,
  version,
  series,
  onOpenDay,
}: {
  todayIso: string;
  version: number;
  /** Постоянный план — одной закреплённой строкой: иначе список — одни субботы. */
  series: PlanSeries | null;
  onOpenDay: (iso: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const to = toIsoDay(addDays(parseIsoDay(todayIso), UPCOMING_DAYS));
  // Свой префикс, а не «planner:my:» месяца: там в кэше лежит ответ с ключом
  // ({key, payload}), здесь — голый, и общий ключ отдал бы не тот вид.
  const { data, error } = useCachedResource(
    `planner:upcoming:${todayIso}:${to}`,
    () => getMyPlans(todayIso, to),
    [todayIso, to, version],
    { errorText: "Не удалось загрузить планы" },
  );
  const items = useMemo(
    () =>
      (data?.items ?? [])
        .filter((item) => item.plan_date >= todayIso && !isSeriesOccurrence(item))
        .sort((a, b) => (a.plan_date === b.plan_date ? 0 : a.plan_date < b.plan_date ? -1 : 1)),
    [data, todayIso],
  );
  if (error) {
    return null;
  }
  if (!data) {
    return null;
  }
  const visible = expanded ? items : items.slice(0, UPCOMING_PREVIEW);
  return (
    <section className="card plans-upcoming" aria-labelledby="plans-upcoming-title">
      <h2 id="plans-upcoming-title" className="section-title">
        Ближайшие
      </h2>
      {series && (
        <button
          type="button"
          className="plans-upcoming-item plans-upcoming-series"
          onClick={() => (series.next_date ? onOpenDay(series.next_date) : onOpenDay(todayIso))}
        >
          <span className="plans-upcoming-when">
            {SERIES_ICON} {series.location.name} — каждую субботу
          </span>
          <span className="plans-upcoming-what">
            {series.sleeping
              ? "на паузе"
              : series.next_date
                ? `ближайшая: ${dayTitle(series.next_date).toLowerCase()}`
                : "будущих суббот нет"}
          </span>
        </button>
      )}
      {items.length === 0 ? (
        !series && (
          <p className="muted">
            Планов пока нет. Нажмите на субботу в календаре — «Планирую пробежать», или «Планирую» на странице
            локации.
          </p>
        )
      ) : (
        <ul className="plans-upcoming-list">
          {visible.map((item) => (
            <li key={item.ref}>
              <button type="button" className="plans-upcoming-item" onClick={() => onOpenDay(item.plan_date)}>
                <span className="plans-upcoming-when">
                  {dayTitle(item.plan_date)}
                  <span className="muted"> · {relativeDayLabel(item.plan_date, todayIso).toLowerCase()}</span>
                </span>
                <span className="plans-upcoming-what">
                  {item.kind === "day" ? "Не бегаю" : (item.location?.name ?? "Локация")}
                  {item.start?.cancelled && <span className="plans-item-cancel"> · старт отменён</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {items.length > UPCOMING_PREVIEW && (
        <button type="button" className="link-button" onClick={() => setExpanded((value) => !value)}>
          {expanded ? "Свернуть" : `Показать все (${formatInt(items.length)})`}
        </button>
      )}
    </section>
  );
}

// ---------- страница ----------

/** Вопрос по якорю из сообщения: «Продолжить» или «Остановить» постоянный план, «Пропустить» день. */
type SeriesAsk = { kind: "resume" } | { kind: "stop" } | { kind: "skip"; date: string };

/**
 * Подпись дня в клетке календаря — название локации мелким текстом, а не
 * точка (замечание Дмитрия 26.10.2026: «так гораздо нагляднее»). Цвет — что с
 * планом: впереди — цветом плана, сбылось — «✓» зелёным, не получилось —
 * серым. Суббота постоянного — «↻ Зеленоград», пробежали в другой локации —
 * «✓ Битца» (у постоянного серых отметок нет). Планов два и больше —
 * «Зеленоград +1». Без планов — «не бегаю» / «не бегали». Приватный план и
 * приватное «Не бегаю» впереди — с замком (их видите только вы).
 */
function dayCaption(plans: PlanItem[], day: PlanItem | null, future: boolean): ReactNode {
  // Сначала строка плана, потом суббота постоянного: поездка важнее правила.
  const main = plans.find((plan) => !isSeriesOccurrence(plan)) ?? plans[0];
  if (!main) {
    if (!day?.rest) {
      return null;
    }
    if (future) {
      return (
        <span className="plans-cap plans-cap-muted">
          {day.is_public === false && <PrivateMark />}
          <span className="plans-cap-name">не бегаю</span>
        </span>
      );
    }
    return restDayRan(day) ? (
      <span className="plans-cap plans-cap-done">✓</span>
    ) : (
      <span className="plans-cap plans-cap-muted">не бегали</span>
    );
  }
  const occurrence = isSeriesOccurrence(main);
  let name = main.location?.name ?? "Локация";
  let tone = "plans-cap-plan";
  let icon = occurrence ? SERIES_ICON : "";
  if (main.past) {
    if (isDone(main)) {
      tone = "plans-cap-done";
      icon = "✓";
    } else if (main.fact?.status === "elsewhere") {
      tone = "plans-cap-done";
      icon = "✓";
      name = main.fact.location?.name ?? "другая локация";
    } else if (occurrence) {
      // Прошедшая суббота постоянного без пробежки — без подписи.
      return null;
    } else {
      tone = main.fact?.status === "missed" ? "plans-cap-muted" : "plans-cap-past";
    }
  }
  const more = plans.length - 1;
  // Значок — отдельно от названия: в узкой клетке он уходит в угол и не
  // съедает строку названия («↻ / Ела-…» на 360; ревью 26.10.2026, UI-5).
  return (
    <span className={`plans-cap ${tone}`}>
      {icon && <span className="plans-cap-icon">{icon}</span>}
      {!main.past && main.is_public === false && <PrivateMark />}
      <span className="plans-cap-name">{name}</span>
      {more > 0 && <span className="plans-cap-more">+{more}</span>}
    </span>
  );
}

/** Замок в клетке: на широкой — перед названием, на узкой — в правом верхнем углу. */
function PrivateMark() {
  return (
    <span className="plans-cap-lock">
      <LockIcon />
    </span>
  );
}

/**
 * «Планы»: календарь месяца с планами на старты и отметками «Не бегаю».
 * Видит только владелец — здесь же приватные планы и «сбылось / не получилось».
 * Над месяцем — постоянный план («↻ каждую субботу») или подсказка завести
 * его; его субботы стоят в сетке сами, своей отметкой «↻».
 */
export function PlansContent() {
  const clientToday = useMemo(() => moscowTodayIso(), []);
  const [month, setMonth] = useRestorableState("plans.month", () => clientToday.slice(0, 7));
  const [picked, setPicked] = useRestorableState<string | null>("plans.day", null);
  const [version, setVersion] = useState(0);
  const [editing, setEditing] = useState<PlanItem | null>(null);
  const [removing, setRemoving] = useState<PlanItem | null>(null);
  const [removeBusy, setRemoveBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  // Правка постоянного плана («Изменить», «Продлить» на карточке).
  const [seriesEdit, setSeriesEdit] = useState<PlanSeries | null>(null);
  // «Убрать» у субботы постоянного: только этот день или остановить план.
  const [seriesRemove, setSeriesRemove] = useState<{ date: string; name: string; rowId: string | null } | null>(
    null,
  );
  const [seriesAsk, setSeriesAsk] = useState<SeriesAsk | null>(null);
  const [seriesBusy, setSeriesBusy] = useState(false);
  const seriesCardRef = useRef<HTMLElement>(null);
  const { snackbar, showSnackbar, dismissSnackbar } = useSnackbar();
  const phone = usePhoneViewport();
  const detailsRef = useRef<HTMLElement>(null);
  // Окно плана «от даты»: день из календаря, локацию выбирают в окне.
  const [planDay, setPlanDay] = useState<string | null>(null);

  const [year, monthNumber] = month.split("-").map(Number);
  const from = `${month}-01`;
  const to = toIsoDay(new Date(year, monthNumber, 0));
  const key = `planner:my:${from}:${to}`;
  // Ответ несёт свой ключ: пока грузится другой месяц, на экране остаётся
  // прежний ответ, и его планы нельзя рисовать в сетке нового. А после правки
  // (тот же ключ) прежний ответ стоит до свежего — без мигания «Загрузка…».
  const { data: loaded, error } = useCachedResource(
    key,
    () => getMyPlans(from, to).then((payload) => ({ key, payload })),
    [key, version],
    { errorText: "Не удалось загрузить планы" },
  );
  const data = loaded?.key === key ? loaded.payload : null;
  const todayIso = data?.today ?? clientToday;

  // Особые даты месяца — отдельной публичной ручкой: подсветить 1 января нужно
  // и тому, у кого на него ещё нет плана, — ради таких дат и заглядывают
  // в календарь. Сбой молча проглатываем: календарь планов работает и без них.
  // Ответ прежнего месяца, пока грузится новый, не мешает: ищем по точной дате.
  const { data: specialData } = useCachedResource(
    `planner:special:${from}:${to}`,
    () => getSpecialDates(from, to),
    [from, to],
  );
  const specialByDate = useMemo(() => {
    const map = new Map<string, SpecialDateInfo>();
    for (const item of specialData?.items ?? []) {
      map.set(item.date, item);
    }
    return map;
  }, [specialData]);

  const byDay = useMemo(() => {
    const map = new Map<string, { plans: PlanItem[]; day: PlanItem | null }>();
    for (const item of data?.items ?? []) {
      const entry = map.get(item.plan_date) ?? { plans: [], day: null };
      if (item.kind === "day") {
        entry.day = item;
      } else {
        entry.plans.push(item);
      }
      map.set(item.plan_date, entry);
    }
    return map;
  }, [data]);

  // День выбирает только человек: при входе и при смене месяца выбора нет.
  // Подставленный «первый доступный» день закрывал бы своей рамкой цвет
  // особой даты — переключил на январь и не видно, что 1-е не типовой старт.
  // После «Назад» день восстанавливается вместе с месяцем.
  const selected = picked && picked.startsWith(month) ? picked : null;

  // Постоянный план и подсказка «9 из 10» — над месяцем.
  const { data: seriesData } = useCachedResource("planner:series", getMySeries, [version]);
  const series = seriesData?.series ?? null;

  const refresh = () => {
    // Планы видны и на «Обзоре», и на странице локации — сбрасываем всё разом.
    dropCached("planner:");
    setVersion((value) => value + 1);
  };

  const rest = useDayRest(refresh);
  // С чего начинается «Публично | Приватно»: у закрытого профиля отметка «Не бегаю» только приватная.
  const { data: plannerSettings } = useCachedResource("planner:settings", getPlannerSettings, []);
  const restVisibility = useRestVisibility(plannerSettings?.forced ?? null, refresh);
  // Имя постоянного на странице локации: только у публичного плана, открытого
  // профиля и не ребёнка. Иначе после паузы обещать «имя снова появится»
  // нельзя — его не было и до неё (как в сообщении «Ещё ходите?»).
  const seriesNamed = Boolean(series?.is_public) && !plannerSettings?.forced;

  const changeMonth = (next: string) => {
    setMonth(next);
    setPicked(null);
  };

  // Из «Ближайших»: открыть день в календаре и докрутить до его деталей —
  // на телефоне они ниже сетки, за экраном.
  const openDay = (iso: string) => {
    setMonth(iso.slice(0, 7));
    setPicked(iso);
    requestAnimationFrame(() => detailsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };

  const confirmRemove = async () => {
    if (!removing?.id) return;
    setRemoveBusy(true);
    setActionError(null);
    try {
      await deletePlan(removing.id);
      setRemoving(null);
      refresh();
    } catch (err) {
      setRemoving(null);
      setActionError(err instanceof Error && err.message ? err.message : "Не удалось убрать план");
    } finally {
      setRemoveBusy(false);
    }
  };

  const runSeries = async (action: "resume" | "stop") => {
    setSeriesBusy(true);
    setActionError(null);
    try {
      if (action === "resume") {
        await resumeSeries();
        showSnackbar({
          variant: "default",
          title: "Постоянный план продолжается",
          message: seriesNamed ? "Имя снова на будущих субботах." : "План снова в силе на будущих субботах.",
        });
      } else {
        await stopSeries();
        showSnackbar({
          variant: "default",
          title: "Постоянный план остановлен",
          message: "Будущие субботы убраны, прошлые остались в истории.",
        });
      }
      setSeriesAsk(null);
      refresh();
    } catch (err) {
      setSeriesAsk(null);
      setActionError(errorText(err, "Не удалось изменить постоянный план"));
    } finally {
      setSeriesBusy(false);
    }
  };

  // Команды в якоре — из сообщений «Ещё ходите?» и напоминания: выполняем
  // один раз, когда известен постоянный план, и снимаем якорь — обновление
  // страницы и «Назад» не должны повторять «Остановить».
  const commandRef = useRef<PlansHashCommand | null | undefined>(undefined);
  useEffect(() => {
    if (commandRef.current === undefined) {
      commandRef.current = plansHashCommand(window.location.hash);
      if (commandRef.current) {
        window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
      }
    }
    const command = commandRef.current;
    if (!command || !seriesData) {
      return;
    }
    commandRef.current = null;
    if (command.kind === "skip") {
      openDay(command.date);
      setSeriesAsk({ kind: "skip", date: command.date });
      return;
    }
    // Открыто окно подтверждения — фокус в нём (autoFocusConfirm), а не на
    // карточке под затемнением: Enter должен подтверждать.
    let asking = false;
    if (command.kind === "series-resume" && series?.sleeping) {
      setSeriesAsk({ kind: "resume" });
      asking = true;
    } else if (command.kind === "series-stop" && series) {
      setSeriesAsk({ kind: "stop" });
      asking = true;
    }
    requestAnimationFrame(() => {
      seriesCardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      if (!asking) {
        seriesCardRef.current?.focus({ preventScroll: true });
      }
    });
    // openDay — новая функция на каждый рендер; команда разовая.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seriesData, series]);

  // announce — новый план из окна «от даты»: турист расписывает месяц по
  // дням, и короткое «План сохранён» подтверждает каждый.
  const handleModalSaved = (plan: PlanItem | null, outcome?: PlanModalOutcome, announce = false) => {
    if (outcome?.series || outcome?.seriesStopped || outcome?.displaced || outcome?.seriesSleeping) {
      const content = planOutcomeSnackbar(plan, outcome, plan?.location?.name ?? series?.location.name ?? "", {
        allPlansHref: PORTAL_CABINET_PLANS_HREF,
        onResume: () => {
          dismissSnackbar();
          void runSeries("resume");
        },
      });
      showSnackbar({ variant: "default", ...content });
    } else if (announce && plan) {
      showSnackbar({
        variant: "default",
        title: "План сохранён",
        message: `${planDayLabel(plan.plan_date, true)} · ${plan.location?.name ?? "локация"}`,
      });
    }
    refresh();
  };

  const dayView = (iso: string): CalendarDayView => {
    const entry = byDay.get(iso);
    const plans = entry?.plans ?? [];
    const day = entry?.day ?? null;
    const specialDate = specialByDate.get(iso) ?? null;
    const special = specialDate !== null || plans.some((plan) => plan.start?.kind === "special");
    // Нажимаются только дни стартов — субботы и особые даты (решение Дмитрия
    // 27.10.2026). Будний день с чем-то записанным (план с прежних времён,
    // снятая особая дата) тоже открывается — иначе его не убрать.
    const active = special || isSaturdayIso(iso) || entry !== undefined;
    return {
      active,
      label: dayAriaLabel(
        iso,
        todayIso,
        plans,
        day,
        specialDate?.title ?? plans.find((plan) => plan.start?.kind === "special")?.start?.title ?? null,
      ),
      className: special ? "plans-day-special" : active ? undefined : "plans-day-off",
      caption: dayCaption(plans, day, iso >= todayIso),
    };
  };

  const selectedEntry = selected ? byDay.get(selected) : undefined;
  const monthItems = [...byDay.values()].flatMap((entry) => entry.plans);
  const monthHasSeries = Boolean(series) || monthItems.some(isSeriesOccurrence);
  // Замок в легенде — только когда в месяце есть что им помечено.
  const monthHasPrivate = [...byDay.entries()].some(
    ([iso, entry]) =>
      iso >= todayIso &&
      (entry.plans.some((plan) => !plan.past && plan.is_public === false) ||
        Boolean(entry.day?.rest && entry.day.is_public === false)),
  );
  const monthHasSpecial =
    [...specialByDate.keys()].some((iso) => iso.startsWith(month)) ||
    [...byDay.values()].some((entry) => entry.plans.some((plan) => plan.start?.kind === "special"));

  // ym-hide-content: календарь и приватные планы вебвизор Метрики не
  // пишет — здесь всё «видите только вы» (ревью 27.10.2026, PRIV-5), как у
  // страниц гостей оргкабинета.
  return (
    <div className="portal-cab-stack plans-page ym-hide-content">
      {series ? (
        <SeriesCard
          ref={seriesCardRef}
          series={series}
          todayIso={todayIso}
          busy={seriesBusy}
          onEdit={() => setSeriesEdit(series)}
          onStop={() => setSeriesAsk({ kind: "stop" })}
          onResume={() => setSeriesAsk({ kind: "resume" })}
          onOpenDay={openDay}
        />
      ) : null}
      {/* Смонтирована всегда: после «Планирую каждую субботу» подсказка
          уходит, а снэкбар «Отменить» остаётся. */}
      <SeriesHintCard hint={series ? null : (seriesData?.hint ?? null)} onChanged={refresh} />

      {phone && <UpcomingPlans todayIso={todayIso} version={version} series={series} onOpenDay={openDay} />}

      <section className="card plans-month-card">
        <MonthCalendar
          month={month}
          todayIso={todayIso}
          selected={selected}
          onSelect={setPicked}
          onMonthChange={changeMonth}
          dayView={dayView}
          highlightSaturdays
        />
        {/* Подписи в клетках говорят сами («Зеленоград», «не бегаю»), в
            легенде — только что значат цвета. */}
        <ul className="plans-legend" aria-label="Обозначения">
          <li>
            <span className="plans-cap plans-cap-plan plans-legend-cap">Локация</span> план
          </li>
          {monthHasSeries && (
            <li>
              <span className="plans-cap plans-cap-plan plans-legend-cap">{SERIES_ICON}</span> постоянный план
            </li>
          )}
          {monthHasPrivate && (
            <li>
              <span className="plans-cap plans-cap-plan plans-legend-cap plans-legend-lock">
                <LockIcon />
              </span>{" "}
              приватно — видите только вы
            </li>
          )}
          <li>
            <span className="plans-cap plans-cap-done plans-legend-cap">✓</span> пробежали
          </li>
          <li>
            <span className="plans-cap plans-cap-muted plans-legend-cap">Локация</span> не получилось
          </li>
          <li>
            <span className="plans-legend-sat" aria-hidden="true" /> субботы
          </li>
          {monthHasSpecial && (
            <li>
              <span className="plans-legend-special" aria-hidden="true" /> особые даты — дополнительные старты (1 января и
              другие)
            </li>
          )}
        </ul>

        <section ref={detailsRef} className="plans-details" aria-live="polite">
          {error && !data ? (
            <p className="error-text">{error}</p>
          ) : !data ? (
            <p className="muted">Загрузка…</p>
          ) : selected ? (
            <DayPanel
              iso={selected}
              todayIso={todayIso}
              plans={selectedEntry?.plans ?? []}
              day={selectedEntry?.day ?? null}
              specialDate={specialByDate.get(selected) ?? null}
              rest={rest}
              visibility={restVisibility}
              onPlan={(iso) => {
                setActionError(null);
                setPlanDay(iso);
              }}
              onEdit={(plan) => {
                setActionError(null);
                // Суббота постоянного — правка самого постоянного плана.
                if (isSeriesOccurrence(plan) && series) {
                  setSeriesEdit(series);
                } else {
                  setEditing(plan);
                }
              }}
              onRemove={(plan, dayRows) => {
                setActionError(null);
                // Суббота постоянного (или его закреплённая строка — одна на
                // день): «только этот день» или «остановить план».
                const lone = plan.series !== null && plan.series !== undefined && dayRows.length <= 1;
                if (isSeriesOccurrence(plan) || (lone && !plan.past)) {
                  setSeriesRemove({
                    date: plan.plan_date,
                    name: plan.location?.name ?? series?.location.name ?? "Локация",
                    rowId: plan.id,
                  });
                } else {
                  setRemoving(plan);
                }
              }}
              onClear={() => setPicked(null)}
            />
          ) : (
            <p className="muted plans-details-hint">
              Нажмите на субботу или особую дату: «Планирую пробежать» или «Не бегаю».
            </p>
          )}
          {actionError && (
            <p className="error-text" role="alert">
              {actionError}
            </p>
          )}
        </section>
      </section>

      <PlanNotificationsSection />

      {editing?.location && (
        <PlanModal
          identityKey={editing.location.key}
          locationName={editing.location.name}
          initialDate={editing.plan_date}
          existing={editing}
          onClose={() => setEditing(null)}
          onSaved={(plan, outcome) => {
            setEditing(null);
            if (plan) {
              setPicked(plan.plan_date);
              setMonth(plan.plan_date.slice(0, 7));
            }
            handleModalSaved(plan, outcome);
          }}
        />
      )}

      {/* «Планирую пробежать» у дня: локацию выбирают в окне. После
          сохранения день остаётся выбранным — следующий выбирают кликом. */}
      {planDay && (
        <PlanModal
          identityKey={null}
          initialDate={planDay}
          onClose={() => setPlanDay(null)}
          onSaved={(plan, outcome) => {
            setPlanDay(null);
            if (plan) {
              setPicked(plan.plan_date);
              setMonth(plan.plan_date.slice(0, 7));
            }
            handleModalSaved(plan, outcome, true);
          }}
        />
      )}

      {seriesEdit && (
        <PlanModal
          identityKey={seriesEdit.location.key}
          locationName={seriesEdit.location.name}
          initialMode="series"
          onClose={() => setSeriesEdit(null)}
          onSaved={(plan, outcome) => {
            setSeriesEdit(null);
            handleModalSaved(plan, outcome);
          }}
        />
      )}

      {rest.confirm}

      {seriesRemove && (
        <SeriesDayRemoveModal
          date={seriesRemove.date}
          locationName={seriesRemove.name}
          rowId={seriesRemove.rowId}
          onCancel={() => setSeriesRemove(null)}
          onDone={({ stopped }) => {
            setSeriesRemove(null);
            if (stopped) {
              showSnackbar({
                variant: "default",
                title: "Постоянный план остановлен",
                message: "Будущие субботы убраны, прошлые остались в истории.",
              });
            }
            refresh();
          }}
        />
      )}

      {/* Поездка вытеснила субботу постоянного — вернуть её или «Не бегаю» (Q07). */}
      {removing?.displaces && (
        <TripRemoveModal
          plan={removing}
          onCancel={() => setRemoving(null)}
          onDone={() => {
            setRemoving(null);
            refresh();
          }}
        />
      )}

      <ConfirmModal
        open={seriesAsk !== null}
        title={
          seriesAsk?.kind === "resume"
            ? "Продолжить постоянный план?"
            : seriesAsk?.kind === "stop"
              ? "Остановить постоянный план?"
              : "Пропустить этот старт?"
        }
        confirmLabel={seriesAsk?.kind === "resume" ? "Продолжить" : seriesAsk?.kind === "stop" ? "Остановить" : "Пропустить"}
        cancelLabel={seriesAsk?.kind === "resume" ? "Не сейчас" : "Оставить"}
        variant={seriesAsk?.kind === "stop" ? "danger" : "default"}
        confirmLoading={seriesBusy || rest.busy}
        autoFocusConfirm
        hideFromWebvisor
        onConfirm={() => {
          if (seriesAsk?.kind === "skip") {
            const day = byDay.get(seriesAsk.date);
            rest.markRest(seriesAsk.date, day?.plans ?? []);
            setSeriesAsk(null);
          } else if (seriesAsk) {
            void runSeries(seriesAsk.kind);
          }
        }}
        onCancel={() => setSeriesAsk(null)}
      >
        {seriesAsk?.kind === "resume" && series
          ? `${series.location.name}: ${seriesNamed ? "имя снова появится" : "план снова в силе"} на будущих субботах.`
          : seriesAsk?.kind === "stop" && series
            ? `${series.location.name}: будущие субботы уйдут из плана, прошлые останутся в истории. Отметки «Не бегаю» останутся.`
            : seriesAsk?.kind === "skip"
              ? `${dayTitle(seriesAsk.date)} — «Не бегаю»: имени на локации в этот день не будет, напоминания тоже. Вернуть — «Отменить» в деталях дня.`
              : ""}
      </ConfirmModal>

      <Snackbar
        open={snackbar.open}
        title={snackbar.title}
        variant={snackbar.variant}
        onDismiss={dismissSnackbar}
        hideFromWebvisor
      >
        {snackbar.message}
      </Snackbar>

      <ConfirmModal
        open={removing !== null && !removing.displaces}
        title="Убрать план?"
        confirmLabel="Убрать"
        cancelLabel="Оставить"
        variant="danger"
        confirmLoading={removeBusy}
        hideFromWebvisor
        onConfirm={() => void confirmRemove()}
        onCancel={() => setRemoving(null)}
      >
        {removing
          ? `${dayTitle(removing.plan_date)} — ${removing.location?.name ?? "локация"}. План исчезнет из календаря.`
          : ""}
      </ConfirmModal>
    </div>
  );
}
