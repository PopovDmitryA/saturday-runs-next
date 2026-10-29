import { useEffect, useId, useRef, useState } from "react";
import { Snackbar } from "../../components/Snackbar";
import { useCachedResource } from "../../hooks/useCachedResource";
import { useRestorableState } from "../../hooks/useRestorableState";
import { useSnackbar } from "../../hooks/useSnackbar";
import { dropCached } from "../../lib/dataCache";
import { formatInt } from "../../lib/format";
import { goToLogin } from "../../lib/loginReturn";
import { PORTAL_CABINET_PLANS_HREF } from "../../lib/portalRoutes";
import { useOptionalUser } from "../../lib/useOptionalUser";
import { useDayRest, type DayRestControls } from "./DayRest";
import { PlanModal, planDayLabel } from "./PlanModal";
import { PlanDateTabs } from "./PlanDateTabs";
import { runToText } from "./placeCase";
import { planDateFromHash } from "./plannerLinks";
import { PlanPeopleCapped } from "./PlanPeople";
import { getLocationPlans, type PlanItem, type PlanSeries } from "./plannerApi";
import { formatDayMonth, formatDayMonthShort } from "./plannerDates";
import {
  planOutcomeSnackbar,
  resumeSeriesQuietly,
  SERIES_ICON,
  SeriesHintCard,
  type PlanModalOutcome,
} from "./seriesParts";
import "./planner.css";

/**
 * «Планируют пробежать» на странице локации: в шапке — заголовок, вкладки
 * ближайших дат, на которые есть планы, и кнопка «Планирую»; под шапкой —
 * имена тех, кто собирается пробежать в выбранную дату, ниже — свой план.
 *
 * План один — «планирую пробежать», без выбора (решение Дмитрия
 * 25.10.2026): весь блок говорит о беге, люди не делятся на колонки.
 *
 * Даты — вкладками, а не списком (вариант 1, выбор Дмитрия 25.10.2026):
 * высота блока не растёт от числа дат, и страница локации не тонет в планах.
 * Вкладки — в строке заголовка, а не своей строкой под ним, и строки «Сб 31
 * октября · 9:00» под ними нет (замечания Дмитрия 25.10.2026): блок по высоте
 * занимал слишком много, а время старта здесь не нужно. Полная дата и
 * название особого старта — в подсказке вкладки.
 *
 * Имена — не больше трёх строк, «и ещё X» раскрывает остальных тут же
 * (решение Дмитрия 27.10.2026: без окна со всем списком); рядом с именем
 * только число пробежек, без «впервые у нас» и юбилеев: их видит
 * организатор в оргкабинете (решение Дмитрия 25.10.2026, блок для участников
 * должен быть компактным).
 *
 * Постоянные («каждую субботу») — в том же списке, у имени значок ↻
 * (решение Дмитрия 27.10.2026: отдельная строка «↻ Каждую субботу: N»
 * избыточна). Свой постоянный план — одной строкой «↻ Вы здесь каждую
 * субботу · Пропущу · Изменить» вместо строк по датам.
 *
 * Отдельный запрос по ключу места, а не поле в ответе страницы: тот лежит в
 * общем кэше на три часа, а планы меняются по клику. Имена — только у тех,
 * чьи планы публичны (сервер считает это при чтении); остальные — числом.
 */

// Системы, у которых бывают планы, — как PLANNABLE_PLATFORMS на сервере.
// parkrun и серии не планируются: блок им не нужен, лишний запрос — тоже.
const PLANNABLE_PLATFORMS = new Set(["five_verst", "s95", "runpark"]);

/** Может ли у локации вообще быть блок планов (дальше решает сервер). */
export function locationMayHavePlans(page: { is_series: boolean; platforms: { platform_code: string }[] }): boolean {
  return !page.is_series && page.platforms.some((item) => PLANNABLE_PLATFORMS.has(item.platform_code));
}

// Своих планов больше двух — остальные под «ещё N»: блок про то, кто
// приедет, а не про собственный список.
const MINE_VISIBLE = 2;

function MinePlanLine({ plan, onEdit }: { plan: PlanItem; onEdit: () => void }) {
  const parts = [planDayLabel(plan.plan_date)];
  if (plan.start?.cancelled) {
    parts.push("старт отменён");
  }
  // Приватный план — имени в списке выше нет, и без пометки казалось бы, что план не встал.
  if (plan.is_public === false) {
    parts.push("приватно");
  }
  // Точка прилипает к слову слева: на узком экране строка не начинается с «·».
  const tail = parts.slice(1).map((part) => `\u00a0· ${part}`).join("");
  return (
    <li className="plan-loc-mine-item">
      <span>
        Ваш план: <strong>{parts[0]}</strong>
        {tail}
      </span>{" "}
      <button type="button" className="link-button plan-loc-edit" onClick={onEdit}>
        Изменить
      </button>
    </li>
  );
}

/**
 * Свой постоянный план — одной строкой: «↻ Вы здесь каждую субботу · Пропущу
 * 31 окт · Изменить». «Пропущу» — про выбранную вкладку: это «Не бегаю» на
 * день, вернуть — «Всё-таки приду». На паузе — «Продолжить».
 *
 * Нажатая «Пропущу» или «Всё-таки приду» уходит из строки, когда ответ
 * перечитан, — фокус встаёт на кнопку взамен (как useRestFocus в «Планах»), а
 * её нет — на «Изменить»: иначе клавиатура и читалка теряли место на <body>.
 */
function SeriesMineLine({
  series,
  date,
  hasOccurrence,
  busy,
  rest,
  onSkip,
  onUnskip,
  onResume,
  onEdit,
}: {
  series: PlanSeries;
  /** Выбранная вкладка; null — вкладок нет. */
  date: string | null;
  /** На выбранной дате у зрителя есть суббота постоянного (или свой план здесь). */
  hasOccurrence: boolean;
  busy: boolean;
  /** «Не бегаю» блока: по settled возвращаем фокус. */
  rest: DayRestControls;
  onSkip: (date: string) => void;
  onUnskip: (date: string) => void;
  onResume: () => void;
  onEdit: () => void;
}) {
  // Пропущенная суббота не на вкладке: если на неё никто больше не собирается,
  // вкладки нет совсем. Поэтому «Всё-таки приду» — у ближайшей пропущенной до
  // выбранной даты включительно, а не только у выбранной.
  const skipped = series.skipped.find((iso) => date === null || iso <= date) ?? null;
  const skipRef = useRef<HTMLButtonElement>(null);
  const unskipRef = useRef<HTMLButtonElement>(null);
  const editRef = useRef<HTMLButtonElement>(null);
  // Строка плана, какой она была в момент действия: пока ответ не перечитан,
  // кнопки взамен ещё нет — ждём новый series, а не уводим фокус раньше.
  const settledFrom = useRef<{ settled: object; series: PlanSeries } | null>(null);
  const { settled, clearSettled } = rest;
  useEffect(() => {
    if (!settled) {
      settledFrom.current = null;
      return;
    }
    let from = settledFrom.current;
    if (from?.settled !== settled) {
      from = { settled, series };
      settledFrom.current = from;
    }
    const target = settled.rest ? (skipped === settled.date ? unskipRef.current : null) : skipRef.current;
    if (target) {
      target.focus();
      clearSettled();
    } else if (series !== from.series) {
      editRef.current?.focus();
      clearSettled();
    }
  }, [settled, clearSettled, series, skipped, date]);
  const edit = (label: string) => (
    <button ref={editRef} type="button" className="link-button plan-loc-edit" onClick={onEdit}>
      {label}
    </button>
  );
  const sep = "\u00a0· ";
  if (series.sleeping) {
    return (
      <li className="plan-loc-mine-item plan-loc-series">
        <span>
          {SERIES_ICON} <strong>Постоянный план на паузе</strong> — на странице вас пока не видно
        </span>
        {sep}
        <button type="button" className="link-button plan-loc-edit" disabled={busy} onClick={onResume}>
          Продолжить
        </button>
        {sep}
        {edit("Изменить")}
      </li>
    );
  }
  if (series.expired) {
    return (
      <li className="plan-loc-mine-item plan-loc-series">
        <span>
          {SERIES_ICON} Срок постоянного плана закончился{series.until ? ` ${formatDayMonth(series.until)}` : ""}
        </span>
        {sep}
        {edit("Продлить")}
      </li>
    );
  }
  const away = date !== null ? series.away.find((item) => item.date === date) : undefined;
  return (
    <li className="plan-loc-mine-item plan-loc-series">
      <span>
        {SERIES_ICON} <strong>Вы здесь каждую субботу</strong>
        {series.until ? ` до ${formatDayMonth(series.until)}` : ""}
        {series.is_public === false ? "\u00a0· приватно" : ""}
      </span>
      {skipped && (
        <>
          {sep}
          <span>{formatDayMonthShort(skipped)} пропускаете</span>
          {sep}
          <button
            ref={unskipRef}
            type="button"
            className="link-button plan-loc-edit"
            disabled={busy}
            onClick={() => onUnskip(skipped)}
          >
            Всё-таки приду
          </button>
        </>
      )}
      {date && away && (
        <>
          {sep}
          <span>
            {formatDayMonthShort(date)} — {runToText(away.location.name)}
          </span>
        </>
      )}
      {date && hasOccurrence && skipped !== date && !away && (
        <>
          {sep}
          <button
            ref={skipRef}
            type="button"
            className="link-button plan-loc-edit"
            disabled={busy}
            onClick={() => onSkip(date)}
          >
            Пропущу {formatDayMonthShort(date)}
          </button>
        </>
      )}
      {sep}
      {edit("Изменить")}
    </li>
  );
}

/**
 * Место под блок, пока едет ответ: шапка и строка под ней. Запрос стартует
 * только после ответа страницы (нужен ключ места), и без места блок вставал
 * в первый экран уже нарисованной страницы, сдвигая всё ниже (ревью
 * 25.10.2026, PERF-1). «Загрузки» нет — блок второстепенный; «Планирую»
 * невидимой — держит высоту шапки, нажать её нельзя.
 */
function PlansPlaceholder() {
  return (
    <section className="card loc-section plan-loc" aria-hidden="true">
      <div className="plan-loc-top">
        <div className="plan-loc-head">
          <h2 className="section-title plan-loc-title">Планируют пробежать</h2>
          <span className="btn primary btn-sm plan-loc-add plan-loc-placeholder">
            <span className="plan-loc-add-icon">📅</span>
            Планирую
          </span>
        </div>
      </div>
      <p className="muted plan-loc-empty">{"\u00a0"}</p>
    </section>
  );
}

export function LocationPlansSection({
  identityKey,
  locationName,
  reserveSpace = false,
}: {
  identityKey: string;
  locationName: string;
  /** Держать место под блок, пока нет ответа: блок у этой локации почти наверняка будет. */
  reserveSpace?: boolean;
}) {
  const user = useOptionalUser();
  const { data: block, reload } = useCachedResource(
    `planner:location:${identityKey}`,
    () => getLocationPlans(identityKey),
    [identityKey],
  );
  // undefined — окно закрыто; null — новый план; PlanItem — правка своего.
  const [modalPlan, setModalPlan] = useState<PlanItem | null | undefined>(undefined);
  // Окно открыто правкой постоянного плана («Изменить» у строки «↻»).
  const [modalSeries, setModalSeries] = useState(false);
  // Раскрытый «и ещё X» — у одной даты; другая вкладка — снова три строки.
  const [expandedDate, setExpandedDate] = useState<string | null>(null);
  const [seriesBusy, setSeriesBusy] = useState(false);
  // Дата, которую окно выберет само: пришли по ссылке /locations/{slug}#plan=YYYY-MM-DD.
  const [modalDate, setModalDate] = useState<string | undefined>(undefined);
  const [mineExpanded, setMineExpanded] = useState(false);
  // Выбранная вкладка даты; null — ближайшая. Переживает «Назад» с профиля
  // человека из блока: имена — ссылки, и без этого возвращались к другой дате.
  const [selectedDate, setSelectedDate] = useRestorableState<string | null>("location.plans.date", null);
  const baseId = useId();
  const { snackbar, showSnackbar, dismissSnackbar } = useSnackbar();

  const refresh = () => {
    // План виден и в кабинете (/plans, «Ближайший план»): их ответы на «назад»
    // после правки устарели.
    dropCached("planner:");
    reload();
  };
  const rest = useDayRest(refresh);

  const openNew = () => {
    if (user === null) {
      goToLogin();
      return;
    }
    setModalDate(undefined);
    setModalSeries(false);
    setModalPlan(null);
  };

  const openSeries = () => {
    setModalDate(undefined);
    setModalSeries(true);
    setModalPlan(null);
  };

  const resume = () => {
    setSeriesBusy(true);
    void resumeSeriesQuietly(refresh, (text) =>
      showSnackbar({ variant: "error", title: "Не получилось", message: text }),
    ).finally(() => setSeriesBusy(false));
  };

  // Ссылка с датой в якоре (#plan=YYYY-MM-DD): окно плана открывается само на
  // этот день. Якорь — разовая команда: снимаем его, чтобы обновление
  // страницы и «Назад» не открывали окно снова. Гостю — вход с возвратом на
  // тот же адрес вместе с якорем.
  useEffect(() => {
    if (!block || user === undefined) {
      return;
    }
    const date = planDateFromHash(window.location.hash);
    if (!date) {
      return;
    }
    if (user === null && block.plannable) {
      goToLogin();
      return;
    }
    window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
    if (!block.plannable) {
      // Локация на паузе, закрылась или это parkrun: окна не будет — говорим
      // почему, а не оставляем человека на странице ни с чем.
      showSnackbar({
        variant: "default",
        title: "Здесь план не поставить",
        message: (
          <>
            {block.reason || "Эту локацию пока нельзя запланировать"} —{" "}
            <a className="snackbar-link" href="/locations">
              выберите другую локацию
            </a>
          </>
        ),
      });
      return;
    }
    const own =
      block.mine.find((item) => item.kind === "plan" && !item.past && item.plan_date === date && item.id !== null) ??
      null;
    setModalDate(date);
    setModalSeries(false);
    setModalPlan(own);
    // showSnackbar — новая функция на каждый рендер; якорь разовый и уже снят.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [block, user]);

  const handleSaved = (plan: PlanItem | null, outcome?: PlanModalOutcome) => {
    setModalPlan(undefined);
    setModalSeries(false);
    refresh();
    const content = planOutcomeSnackbar(plan, outcome, locationName, {
      allPlansHref: PORTAL_CABINET_PLANS_HREF,
      onResume: () => {
        dismissSnackbar();
        resume();
      },
    });
    showSnackbar({ variant: "default", ...content });
  };

  const snackbarNode = (
    <Snackbar
      open={snackbar.open}
      title={snackbar.title}
      variant={snackbar.variant}
      onDismiss={dismissSnackbar}
      hideFromWebvisor
    >
      {snackbar.message}
    </Snackbar>
  );

  // Пока нет ответа — только место под блок (PlansPlaceholder): блок
  // второстепенный, мигать «Загрузкой» над статистикой незачем. Ошибку молча
  // проглатываем.
  if (!block) {
    return (
      <>
        {reserveSpace && <PlansPlaceholder />}
        {snackbarNode}
      </>
    );
  }

  // Свои строки планов; вхождения постоянного — одной строкой «↻» ниже.
  const mine = block.mine.filter((item) => item.kind === "plan" && !item.past && item.id !== null);
  const mySeries = block.my_series ?? null;
  if (!block.plannable && block.dates.length === 0 && mine.length === 0 && !mySeries) {
    return snackbarNode;
  }

  // Выбранной даты не стало (план убрали, ответ обновился) — снова ближайшая.
  const current = block.dates.find((item) => item.date === selectedDate) ?? block.dates[0];
  const tabId = (date: string) => `${baseId}-tab-${date}`;
  const panelId = (date: string) => `${baseId}-panel-${date}`;

  return (
    <section className="card loc-section plan-loc" aria-labelledby="plan-loc-title">
      {/* Шапка одной строкой: заголовок, сразу за ним вкладки дат, у правого
          края — «Планирую». Узкому блоку вкладки уходят второй строкой. */}
      <div className="plan-loc-top">
        <div className="plan-loc-head">
          <h2 className="section-title plan-loc-title" id="plan-loc-title">
            Планируют пробежать
          </h2>
          {current && (
            <PlanDateTabs
              dates={block.dates}
              current={current}
              tabId={tabId}
              panelId={panelId}
              onSelect={(date) => {
                setSelectedDate(date);
                setExpandedDate(null);
              }}
            />
          )}
          {block.plannable && (
            <button type="button" className="btn primary btn-sm plan-loc-add" onClick={openNew}>
              <span className="plan-loc-add-icon" aria-hidden="true">
                📅
              </span>
              Планирую
            </button>
          )}
        </div>
      </div>

      {block.dates.length === 0 && block.plannable && (
        <p className="muted plan-loc-empty">Собираетесь к нам? Отметьте дату</p>
      )}

      {/* Панели всех дат — стопкой в одной клетке сетки, видна только
          выбранная: на широком блоке высота — по самой высокой дате (а она
          не выше трёх строк имён), и при переключении вкладок блок не
          меняется на строку (замечание Дмитрия 25.10.2026: имена с числами
          переносятся у дат по-разному). Скрытые не видны глазу (visibility),
          клавиатуре (inert) и читалкам (aria-hidden). На узком блоке скрытые
          не занимают места — см. .plan-loc-panels в planner.css. */}
      {current && (
        <div className="plan-loc-panels">
          {block.dates.map((item) => {
            const active = item.date === current.date;
            return (
              <div
                key={item.date}
                className="plan-loc-panel"
                role="tabpanel"
                id={panelId(item.date)}
                aria-labelledby={tabId(item.date)}
                aria-hidden={active ? undefined : true}
                inert={!active}
              >
                {/* key — став выбранной, дата монтируется заново и меряет,
                    сколько имён влезает в три строки: на телефоне скрытые
                    даты не рисуются (display: none), мерить их было нечем.
                    Раскрытая дата растит стопку панелей — так и задумано:
                    остальные свёрнуты, и при смене вкладки она тоже. */}
                <PlanPeopleCapped
                  key={active ? "active" : "idle"}
                  audience={item}
                  expanded={active && expandedDate === item.date}
                  onExpandedChange={(open) => setExpandedDate(open ? item.date : null)}
                />
              </div>
            );
          })}
        </div>
      )}

      {(mine.length > 0 || mySeries) && (
        // ym-hide-content: свои планы (и приватные) вебвизор Метрики не пишет —
        // «видите только вы» (ревью 27.10.2026, PRIV-5).
        <ul className="plan-loc-mine ym-hide-content">
          {mySeries && (
            <SeriesMineLine
              series={mySeries}
              date={current?.date ?? null}
              hasOccurrence={Boolean(current && block.mine.some((item) => item.plan_date === current.date && !item.past))}
              busy={rest.busy || seriesBusy}
              rest={rest}
              // Планы дня блок знает только на этой локации, а «Пропущу» снимает
              // все: на особую дату рядом с закреплённым домом бывают поездки.
              // Без списка вопрос задаёт сервер (409) — с полным перечнем.
              onSkip={(date) => rest.markRest(date, [])}
              onUnskip={(date) => rest.clearRest(date)}
              onResume={resume}
              onEdit={openSeries}
            />
          )}
          {(mineExpanded ? mine : mine.slice(0, MINE_VISIBLE)).map((plan) => (
            <MinePlanLine
              key={plan.ref}
              plan={plan}
              onEdit={() => {
                setModalDate(undefined);
                setModalSeries(false);
                setModalPlan(plan);
              }}
            />
          ))}
          {!mineExpanded && mine.length > MINE_VISIBLE && (
            <li>
              <button type="button" className="link-button plan-loc-mine-more" onClick={() => setMineExpanded(true)}>
                ещё {formatInt(mine.length - MINE_VISIBLE)}
              </button>
            </li>
          )}
        </ul>
      )}

      {rest.error && (
        <p className="error-text" role="alert">
          {rest.error}
        </p>
      )}

      {/* Подсказка «9 из 10» — только своей локации; смонтирована всегда:
          после нажатия подсказка уходит, а снэкбар «Отменить» остаётся. */}
      {user && <SeriesHintCard hint={mySeries ? null : (block.series_hint ?? null)} here onChanged={refresh} />}

      {!block.plannable && block.reason && <p className="muted plan-loc-reason">{block.reason}</p>}

      {modalPlan !== undefined && (
        <PlanModal
          identityKey={identityKey}
          locationName={locationName}
          initialDate={modalDate}
          existing={modalPlan}
          initialMode={modalSeries ? "series" : "once"}
          onClose={() => {
            setModalPlan(undefined);
            setModalSeries(false);
          }}
          onSaved={handleSaved}
        />
      )}
      {rest.confirm}
      {snackbarNode}
    </section>
  );
}
