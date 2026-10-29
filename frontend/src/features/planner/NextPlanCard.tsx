import { lazy, Suspense, useRef, useState, type ReactNode } from "react";
import { LocationNameLink } from "../../components/LocationNameLink";
import { useCachedResource } from "../../hooks/useCachedResource";
import { dropCached } from "../../lib/dataCache";
import { pluralFormRu } from "../../lib/format";
import { PORTAL_CABINET_PLANS_HREF } from "../../lib/portalRoutes";
import { useDayRest, useRestFocus } from "./DayRest";
import { getNextPlan, type NextStartDay, type PlanItem } from "./plannerApi";
import { resumeSeriesQuietly, SERIES_ICON } from "./seriesText";
import { daysUntil, formatWeekdayDayMonth, moscowTodayIso, parseIsoDay, planDayLabel } from "./plannerDates";
import "./plansPage.css";

// Подсказка «9 из 10» и окно плана тянут стили окна и поиск локаций — грузим
// их, только когда они нужны: карточка стоит в стартовом чанке.
const SeriesHintCard = lazy(() => import("./seriesParts").then((module) => ({ default: module.SeriesHintCard })));
const PlanModal = lazy(() => import("./PlanModal").then((module) => ({ default: module.PlanModal })));

// Подписи плана живут здесь, а не на странице «Планы»: карточка стоит на
// «Обзоре» в стартовом чанке, и тянуть ради пары функций всю страницу с
// календарём и окном правки туда нельзя. Страница и оргкабинет берут их отсюда.

const DAY_FORMS = ["день", "дня", "дней"] as const;

// Сегодня по Москве живёт в plannerDates: оно нужно и окну плана, которому
// тянуть стили «Обзора» незачем. Отсюда — для прежних импортов.
export { moscowTodayIso };

/** «Сегодня», «Завтра», «Через 3 дня». */
export function relativeDayLabel(iso: string, todayIso: string): string {
  const days = daysUntil(iso, parseIsoDay(todayIso));
  if (days <= 0) return "Сегодня";
  if (days === 1) return "Завтра";
  return `Через ${days} ${pluralFormRu(days, DAY_FORMS)}`;
}

/**
 * «сб 31 октября» или «чт 1 января · Новогодний старт». Времени старта нет —
 * план про дату (решение Дмитрия 26.10.2026).
 */
export function planWhenLine(plan: PlanItem): string {
  const day = formatWeekdayDayMonth(plan.plan_date);
  const start = plan.start;
  return start?.kind === "special" && start.title ? `${day} · ${start.title}` : day;
}

/** «сб 31 октября» или «чт 1 января · Новогодний старт». */
function startDayLabel(day: NextStartDay): string {
  const label = planDayLabel(day.date);
  return day.kind === "special" && day.title ? `${label} · ${day.title}` : label;
}

/**
 * «сб 31 октября» или «пт 1 января 2027 (Новогодний старт)» — внутри фразы
 * «В … не бегаете»: через « · » название встало бы между датой и глаголом
 * (ревью 26.10.2026, UI-D5).
 */
function restDayLabel(day: NextStartDay): string {
  const label = planDayLabel(day.date);
  return day.kind === "special" && day.title ? `${label} (${day.title})` : label;
}

/** «Дальше: сб 7 ноября · Кузьминки» — план после ближайшего старта. */
function LaterPlan({ plan }: { plan: PlanItem }) {
  return (
    <p className="plans-next-later muted">
      Дальше: {planWhenLine(plan)}
      {plan.location && (
        <>
          {" · "}
          <LocationNameLink name={plan.location.name} slug={plan.location.slug} />
        </>
      )}
    </p>
  );
}

/**
 * Тихая пометка у приватного плана — как «приватно» в строке «Ваш план» на
 * странице локации. У публичного строки нет: публичный — обычный случай, а
 * описаний «как это видят другие» Дмитрий не просил (ревью 27.10.2026,
 * UI-9). Меняют выбор в окне плана.
 */
function PlanPrivateLine({ isPublic }: { isPublic: boolean | undefined }) {
  return isPublic === false ? <p className="plans-next-later muted">Приватный план</p> : null;
}

/**
 * «Ближайший план» на «Обзоре» — про ближайший день старта: эта суббота или
 * особая дата раньше неё (решение Дмитрия 26.10.2026: планировщиком можно
 * пользоваться как личным планером).
 *
 * - На этот день есть план — куда и когда.
 * - Отмечено «Не бегаю» — «В сб 31 октября не бегаете · Отменить».
 * - Ни того ни другого — «Планирую» (окно плана на этот день, локацию
 *   выбирают в нём) и «Не бегаю».
 *
 * План на дату дальше ближайшего старта — тихой строкой «Дальше: …».
 *
 * Постоянный план («каждую субботу»): суббота плана — «↻ каждую субботу ·
 * Пропущу · Поеду в другую», план на паузе — «Продолжить», нет плана —
 * подсказка «9 из 10».
 */
export function NextPlanCard() {
  const { data, reload } = useCachedResource("planner:next", getNextPlan, []);
  const [resumeError, setResumeError] = useState<string | null>(null);
  // Подсказку показывали — держим её смонтированной и без подсказки в ответе:
  // после «Планирую каждую субботу» на экране остаётся снэкбар «Отменить».
  const hintShownRef = useRef(false);
  const [resuming, setResuming] = useState(false);
  // Окно плана на ближайший день старта открыто.
  const [planning, setPlanning] = useState(false);
  const refresh = () => {
    // Отметка видна и в «Планах», и на странице локации — сбрасываем всё разом.
    dropCached("planner:");
    reload();
  };
  const rest = useDayRest(refresh);
  const focusRef = useRestFocus(rest, data?.day?.date ?? null, Boolean(data?.day?.rest));

  if (!data?.day) {
    return null;
  }
  const { day, plan } = data;
  const series = data.series ?? null;
  const todayIso = moscowTodayIso();
  // План на ближайший старт — первый по дате; другой (дальше) — «Дальше: …».
  const dayPlan = plan && plan.plan_date === day.date ? plan : null;
  const later = plan && plan.plan_date > day.date ? plan : null;
  // «↻» — по самому плану на карточке: day.series говорит лишь, что среди
  // планов дня есть постоянный, а на особую дату карточка показывает первый
  // по времени — бывает и поездка рядом с закреплённым домом.
  const daySeries = dayPlan ? Boolean(dayPlan.series) : Boolean(day.series);
  const skippedSeries = series && day.rest && series.skipped.includes(day.date) ? series : null;
  const hint = series ? null : (data.series_hint ?? null);
  if (hint) {
    hintShownRef.current = true;
  }
  const resume = () => {
    setResuming(true);
    setResumeError(null);
    void resumeSeriesQuietly(refresh, setResumeError).finally(() => setResuming(false));
  };

  const allLink = (
    <a className="plans-next-all" href={PORTAL_CABINET_PLANS_HREF}>
      Все планы →
    </a>
  );
  const errorLine = rest.error && (
    <p className="error-text" role="alert">
      {rest.error}
    </p>
  );

  let body: ReactNode;
  if (day.planned) {
    const location = dayPlan?.location ?? null;
    body = (
      <>
        <p className="plans-next-text">
          <strong className="plans-next-when">{relativeDayLabel(day.date, todayIso)}</strong>
          {location && (
            <>
              {" · "}
              <LocationNameLink name={location.name} slug={location.slug} />
            </>
          )}
          {" · "}
          {dayPlan ? planWhenLine(dayPlan) : startDayLabel(day)}
          {dayPlan?.start?.cancelled && <span className="plans-next-cancel"> · старт отменён</span>}
          {daySeries && (
            <>
              {" · "}
              <span className="plans-next-series">{SERIES_ICON} каждую субботу</span>
              {" · "}
              <button
                ref={focusRef}
                type="button"
                className="link-button plans-next-undo"
                disabled={rest.busy}
                // Других планов дня карточка не знает — вопрос с полным
                // списком задаст сервер (409), а не «убрать этот» при трёх.
                onClick={() => rest.markRest(day.date, [])}
              >
                Пропущу
              </button>
              {/* Суббота постоянного без своей строки: поездку в другую
                  локацию ставят прямо отсюда, окном от даты. */}
              {!dayPlan?.id && (
                <>
                  {" · "}
                  <button type="button" className="link-button plans-next-undo" onClick={() => setPlanning(true)}>
                    Поеду в другую
                  </button>
                </>
              )}
            </>
          )}
        </p>
        <PlanPrivateLine isPublic={dayPlan?.is_public ?? (daySeries ? series?.is_public : undefined)} />
        {errorLine}
      </>
    );
  } else if (day.rest) {
    body = (
      <>
        <p className="plans-next-text">
          {skippedSeries ? (
            <>
              В {restDayLabel(day)} пропускаете: {skippedSeries.location.name} ·{" "}
            </>
          ) : (
            <>В {restDayLabel(day)} не бегаете · </>
          )}
          <button
            ref={focusRef}
            type="button"
            className="link-button plans-next-undo"
            disabled={rest.busy}
            onClick={() => rest.clearRest(day.date)}
          >
            {skippedSeries ? "Всё-таки приду" : "Отменить"}
          </button>
        </p>
        {later && <LaterPlan plan={later} />}
        {errorLine}
      </>
    );
  } else {
    body = (
      <>
        <p className="plans-next-text">
          <strong className="plans-next-when">{relativeDayLabel(day.date, todayIso)}</strong> ·{" "}
          {startDayLabel(day)} — планов пока нет
        </p>
        <div className="actions-row plans-next-actions">
          <button type="button" className="btn primary btn-sm" onClick={() => setPlanning(true)}>
            Планирую
          </button>
          <button
            ref={focusRef}
            type="button"
            className="btn secondary btn-sm"
            disabled={rest.busy}
            onClick={() => rest.markRest(day.date, [])}
          >
            {rest.busy && <span className="btn-spinner" aria-hidden="true" />}
            Не бегаю
          </button>
        </div>
        {later && <LaterPlan plan={later} />}
        {errorLine}
      </>
    );
  }

  return (
    <section className="card plans-next" aria-label={day.planned ? "Ближайший план" : "Ближайший старт"}>
      <span className="plans-next-icon" aria-hidden="true">
        📅
      </span>
      {/* ym-hide-content: свой план (бывает приватным) вебвизор Метрики не пишет (ревью 27.10.2026, PRIV-5). */}
      <div className="plans-next-body ym-hide-content">
        {body}
        {series?.sleeping && (
          <p className="plans-next-later">
            {SERIES_ICON} Постоянный план — {series.location.name} — на паузе ·{" "}
            <button type="button" className="link-button plans-next-undo" disabled={resuming} onClick={resume}>
              Продолжить
            </button>
          </p>
        )}
        {resumeError && (
          <p className="error-text" role="alert">
            {resumeError}
          </p>
        )}
        {hintShownRef.current && (
          <Suspense fallback={null}>
            <SeriesHintCard hint={hint} onChanged={refresh} compact />
          </Suspense>
        )}
      </div>
      {allLink}
      {/* Окно — при любом виде карточки: 409 («план появился в другой
          вкладке») перерисует её планом, а вопрос должен остаться на экране. */}
      {rest.confirm}
      {planning && (
        <Suspense fallback={null}>
          <PlanModal
            identityKey={null}
            initialDate={day.date}
            onClose={() => setPlanning(false)}
            onSaved={() => {
              setPlanning(false);
              refresh();
            }}
          />
        </Suspense>
      )}
    </section>
  );
}
