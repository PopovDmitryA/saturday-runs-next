import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { DetailModal } from "../../components/DetailModal";
import { LocationNameLink } from "../../components/LocationNameLink";
import { Snackbar } from "../../components/Snackbar";
import { useSnackbar } from "../../hooks/useSnackbar";
import { ApiError } from "../../lib/api";
import { formatInt } from "../../lib/format";
import { useOverlayFocus } from "../portal/nav/useOverlayFocus";
import {
  deletePlan,
  saveDayRest,
  saveSeries,
  snoozeSeriesHint,
  stopSeries,
  type PlanItem,
  type PlanLocation,
  type PlanSeries,
  type SeriesHint,
} from "./plannerApi";
import { planDayLabel } from "./plannerDates";
import { errorText, isSaturday, SERIES_ICON, seriesStatusText, shortDays } from "./seriesText";
import "./planner.css";

export * from "./seriesText";

/**
 * Общее у постоянного плана («каждую субботу») для блока локации, «Планов»,
 * «Обзора» и окна плана: подписи срока, подсказка «9 из 10», окно «убрать
 * поездку» и снэкбары после сохранения.
 *
 * Постоянный план — правило, а не строки (решение Дмитрия 25.10.2026, «как в
 * Outlook»): субботы встают сами, исключения — «Не бегаю» на день и разовый
 * план в другой локации. Убрали поездку — суббота вернулась.
 */

// ---------- что сказать после сохранения ----------

/**
 * Чем кончилось окно плана — для снэкбара родителя. Названия локаций не
 * склоняем («в Зеленограде» из произвольного названия не собрать), поэтому
 * они стоят отдельно, через тире или двоеточие.
 */
export type PlanModalOutcome = {
  /** Разовый план вытеснил субботу постоянного — его локация. */
  displaced?: PlanLocation | null;
  /** План на локацию своего постоянного, а тот на паузе. */
  seriesSleeping?: boolean;
  /** Сохранён постоянный план. */
  series?: PlanSeries | null;
  seriesCreated?: boolean;
  /** Постоянный план на другой локации, который этот заменил. */
  replaced?: PlanLocation | null;
  /** Даты «Пропущу» из окна, которые не отметились. */
  skippedFailed?: string[];
  /** Остановлен постоянный план этой локации. */
  seriesStopped?: PlanLocation | null;
  /** Убрали поездку и отметили «Не бегаю». */
  rested?: boolean;
  /** Убрали поездку — на её дату вернулся постоянный план этой локации. */
  returned?: PlanLocation | null;
  /** Дата убранного плана. */
  removedDate?: string | null;
};

type SnackbarContent = { title: string; message: ReactNode };

/**
 * Снэкбар после окна плана. plan — сохранённый разовый план (null — убран
 * или сохранялся постоянный); locationName — локация окна. resume — кнопка
 * «Продолжить», если разовый план поставлен на локацию постоянного на паузе.
 */
export function planOutcomeSnackbar(
  plan: PlanItem | null,
  outcome: PlanModalOutcome | undefined,
  locationName: string,
  extras: { allPlansHref: string; onResume?: () => void },
): SnackbarContent {
  const allLink = (
    <a className="snackbar-link" href={extras.allPlansHref}>
      Все планы
    </a>
  );
  if (outcome?.series) {
    const series = outcome.series;
    const failed = outcome.skippedFailed ?? [];
    return {
      title: outcome.seriesCreated ? "Постоянный план сохранён" : "Постоянный план изменён",
      message: (
        <>
          {SERIES_ICON} {series.location.name} — {seriesStatusText(series)}
          {outcome.replaced && ` вместо: ${outcome.replaced.name}`}.
          {failed.length > 0 && ` Не получилось пропустить: ${shortDays(failed)}.`} {allLink}
        </>
      ),
    };
  }
  if (outcome?.seriesStopped) {
    return {
      title: "Постоянный план остановлен",
      message: `${outcome.seriesStopped.name}: будущие субботы убраны, прошлые остались в истории.`,
    };
  }
  if (!plan) {
    const day = outcome?.removedDate ? planDayLabel(outcome.removedDate) : null;
    if (outcome?.rested && day) {
      return { title: "План убран", message: `${locationName}. На ${day} отмечено «Не бегаю».` };
    }
    if (outcome?.returned && day) {
      return {
        title: "План убран",
        message: `${locationName}. На ${day} снова в силе постоянный план — ${outcome.returned.name}.`,
      };
    }
    return { title: "План убран", message: locationName };
  }
  const day = planDayLabel(plan.plan_date, true);
  return {
    title: "План сохранён",
    message: (
      <>
        {day} · {locationName}.{" "}
        {outcome?.displaced && (
          <>
            Постоянный план — {outcome.displaced.name} — на эту дату не считаем: вернётся, если уберёте этот план.{" "}
          </>
        )}
        {outcome?.seriesSleeping && extras.onResume && (
          <>
            Постоянный план здесь на паузе ·{" "}
            <button type="button" className="link-button snackbar-link" onClick={extras.onResume}>
              Продолжить
            </button>
            {" · "}
          </>
        )}
        {allLink}
      </>
    ),
  };
}

// ---------- подсказка «9 из 10» ----------

/**
 * «Вы пробежали здесь 9 из 10 последних стартов · ↻ Планирую каждую субботу ·
 * Не сейчас» (Q10, решение Дмитрия 25.10.2026): одно нажатие ставит
 * постоянный план без срока с ближайшей субботы, снэкбар даёт «Отменить».
 * «Не сейчас» прячет подсказку на 30 дней на всех устройствах.
 *
 * Публичность — как у профиля (сервер берёт её сам, когда поля нет);
 * снэкбар её называет и даёт «Сделать приватным».
 *
 * Компонент держите смонтированным и без подсказки (hint=null): после нажатия
 * подсказка пропадает из ответа, а снэкбар «Отменить» должен остаться на
 * экране.
 */
export function SeriesHintCard({
  hint,
  here = false,
  onChanged,
  compact = false,
}: {
  hint: SeriesHint | null;
  /** Подсказка на странице самой локации: «здесь», без названия. */
  here?: boolean;
  onChanged: () => void;
  /** Внутри карточки «Обзора» — без своей рамки. */
  compact?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { snackbar, showSnackbar, dismissSnackbar } = useSnackbar();
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;

  const undo = useCallback(async () => {
    dismissSnackbar();
    try {
      await stopSeries();
      showSnackbar({ variant: "default", title: "Постоянный план отменён", message: "Подсказку уберём на месяц." });
    } catch (err) {
      showSnackbar({ variant: "error", title: "Не удалось отменить", message: errorText(err, "Попробуйте ещё раз") });
    }
    onChangedRef.current();
    // dismissSnackbar и showSnackbar — новые на каждый рендер, но делают одно и то же.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Одно нажатие окна плана не открывает, и переключателя «Публично |
  // Приватно» человек не видел: снэкбар называет, что выбрано по умолчанию
  // (как профиль), и даёт сделать приватным тут же (ревью 27.10.2026, PRIV-4).
  const makePrivate = useCallback(async (key: string) => {
    dismissSnackbar();
    try {
      await saveSeries({ key, is_public: false });
      showSnackbar({ variant: "default", title: "Постоянный план приватный", message: "Его видите только вы." });
    } catch (err) {
      showSnackbar({ variant: "error", title: "Не удалось изменить", message: errorText(err, "Попробуйте ещё раз") });
    }
    onChangedRef.current();
    // dismissSnackbar и showSnackbar — новые на каждый рендер, но делают одно и то же.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const announce = (series: PlanSeries) => {
    showSnackbar({
      variant: "default",
      title: "Постоянный план",
      message: (
        <>
          {SERIES_ICON} {series.location.name} — каждую субботу, {series.is_public ? "публично" : "приватно"}.{" "}
          {series.is_public && (
            <>
              <button
                type="button"
                className="link-button snackbar-link"
                onClick={() => void makePrivate(series.location.key)}
              >
                Сделать приватным
              </button>
              {" · "}
            </>
          )}
          <button type="button" className="link-button snackbar-link" onClick={() => void undo()}>
            Отменить
          </button>
        </>
      ),
    });
  };

  const accept = async () => {
    if (!hint || busy) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await saveSeries({ key: hint.location.key });
      announce(result.series);
      onChangedRef.current();
    } catch (err) {
      setError(errorText(err, "Не удалось сохранить постоянный план"));
    } finally {
      setBusy(false);
    }
  };

  const snooze = async () => {
    if (busy) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await snoozeSeriesHint();
      onChangedRef.current();
    } catch (err) {
      setError(errorText(err, "Не удалось убрать подсказку"));
    } finally {
      setBusy(false);
    }
  };

  // «из N последних стартов»: правило подсказки — не меньше шести стартов, и
  // после «из 6…10 последних» форма всегда «стартов».
  const text = hint ? (
    here ? (
      <>
        Вы пробежали здесь {formatInt(hint.runs)} из {formatInt(hint.of)} последних стартов.
      </>
    ) : (
      <>
        Вы пробежали {formatInt(hint.runs)} из {formatInt(hint.of)} последних стартов на локации{" "}
        <LocationNameLink name={hint.location.name} slug={hint.location.slug} />.
      </>
    )
  ) : null;

  return (
    <>
      {hint && (
        <div className={compact ? "plan-series-hint plan-series-hint-compact" : "plan-series-hint"}>
          <p className="plan-series-hint-text">
            {text} Отмечать вас каждую субботу?
          </p>
          <div className="plan-series-hint-actions">
            <button type="button" className="btn primary btn-sm" disabled={busy} onClick={() => void accept()}>
              {busy && <span className="btn-spinner" aria-hidden="true" />}
              {SERIES_ICON} Планирую каждую субботу
            </button>
            <button type="button" className="link-button plan-series-hint-later" disabled={busy} onClick={() => void snooze()}>
              Не сейчас
            </button>
          </div>
          {error && (
            <p className="error-text" role="alert">
              {error}
            </p>
          )}
        </div>
      )}
      <Snackbar
        open={snackbar.open}
        title={snackbar.title}
        variant={snackbar.variant}
        onDismiss={dismissSnackbar}
        hideFromWebvisor
      >
        {snackbar.message}
      </Snackbar>
    </>
  );
}

// ---------- убрать субботу постоянного плана ----------

/**
 * Фокус в окнах «убрать»: при открытии — на кнопку выбора, Tab — по кругу
 * внутри окна, после закрытия — обратно на «Убрать». Без этого фокус
 * оставался под затемнением, и Tab с Enter меняли день в календаре за окном
 * (как у окна плана, ревью 27.10.2026, UI-4). Esc закрывает DetailModal.
 */
function useRemoveModalFocus(initialSelector: string) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  useOverlayFocus({
    open: true,
    containers: [panelRef],
    initial: () => panelRef.current?.querySelector<HTMLElement>(initialSelector) ?? null,
    onEscape: () => undefined,
  });
  return panelRef;
}

/**
 * «Убрать» у субботы постоянного плана в деталях дня (отзыв Дмитрия
 * 27.10.2026: «не нашёл, как снять постоянную пробежку — могу только разово
 * сказать «не бегаю»»). Два явных выхода: только этот день — отметка «Не
 * бегаю» (следующие субботы остаются), или остановить постоянный план
 * целиком — будущие субботы уходят, прошлые остаются в истории.
 *
 * rowId — разовый план на точке постоянного в этот день (закреплённая
 * суббота): «Только этот день» убирает и его, «Остановить» — тоже, иначе
 * он остался бы планом на остановленной точке.
 */
export function SeriesDayRemoveModal({
  date,
  locationName,
  rowId,
  onDone,
  onCancel,
}: {
  date: string;
  locationName: string;
  rowId: string | null;
  /** rest — отметили «Не бегаю» на день; stopped — остановили постоянный план. */
  onDone: (result: { rest: boolean; stopped: boolean }) => void;
  onCancel: () => void;
}) {
  const [busy, setBusy] = useState<"day" | "stop" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const onCancelRef = useRef(onCancel);
  onCancelRef.current = onCancel;
  const busyRef = useRef(busy);
  busyRef.current = busy;
  const close = useCallback(() => {
    if (!busyRef.current) {
      onCancelRef.current();
    }
  }, []);
  const panelRef = useRemoveModalFocus(".plan-series-remove-option");
  // Суббота — «эту субботу», особая дата в постоянном плане бывает и в пятницу.
  const thisDay = isSaturday(date) ? "Только эту субботу" : "Только этот день";

  const skipDay = async () => {
    setBusy("day");
    setError(null);
    try {
      // replace — закреплённую субботу (разовый план дома) уберёт тот же запрос.
      await saveDayRest(date, true);
      setBusy(null);
      onDone({ rest: true, stopped: false });
    } catch (err) {
      setBusy(null);
      setError(errorText(err, "Не удалось отметить «Не бегаю»"));
    }
  };

  const stopAll = async () => {
    setBusy("stop");
    setError(null);
    try {
      await stopSeries();
      if (rowId) {
        try {
          await deletePlan(rowId);
        } catch (err) {
          // 404 — строки уже нет; остальное не мешает остановке.
          if (!(err instanceof ApiError && err.status === 404)) {
            throw err;
          }
        }
      }
      setBusy(null);
      onDone({ rest: false, stopped: true });
    } catch (err) {
      setBusy(null);
      setError(errorText(err, "Не удалось остановить постоянный план"));
    }
  };

  const footer = (
    <div className="plan-modal-footer">
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <div className="plan-modal-actions">
        <button type="button" className="btn btn-ghost" disabled={busy !== null} onClick={close}>
          Отмена
        </button>
      </div>
    </div>
  );

  return (
    <DetailModal
      open
      title="Убрать пробежку?"
      width="narrow"
      onClose={close}
      footer={footer}
      panelRef={panelRef}
      hideFromWebvisor
    >
      <div className="plan-modal plan-series-remove">
        <p>
          <strong>{planDayLabel(date)}</strong> — {SERIES_ICON} {locationName}, постоянный план.
        </p>
        <button
          type="button"
          className="plan-series-remove-option"
          disabled={busy !== null}
          onClick={() => void skipDay()}
        >
          <span className="plan-series-remove-title">
            {busy === "day" && <span className="btn-spinner" aria-hidden="true" />}
            {thisDay}
          </span>
          <span className="plan-series-remove-text">
            В этот день — «Не бегаю», имени на локации не будет. Постоянный план продолжается.
          </span>
        </button>
        <button
          type="button"
          className="plan-series-remove-option plan-series-remove-stop"
          disabled={busy !== null}
          onClick={() => void stopAll()}
        >
          <span className="plan-series-remove-title">
            {busy === "stop" && <span className="btn-spinner" aria-hidden="true" />}
            Остановить постоянный план
          </span>
          <span className="plan-series-remove-text">
            {locationName}: будущие субботы уйдут из плана, прошлые останутся в истории.
          </span>
        </button>
      </div>
    </DetailModal>
  );
}

// ---------- убрать поездку (Q07) ----------

/**
 * Окно «Убрать план» у разового плана, вытеснившего субботу постоянного (Q07,
 * решение Дмитрия 25.10.2026: «нужна модалка, что вернётся постоянная
 * локация в план, и выбор: вообще не бегаю в указанную субботу или вернуть
 * свою постоянную локацию»). Перенос поездки на другую дату — без окна:
 * прежняя дата вернётся к постоянному плану сама.
 *
 * На дате есть ещё планы (особый день, данные устарели) — сервер на «Не бегаю»
 * отвечает 409 готовым вопросом «Убрать план и отметить «Не бегаю»?»: окно
 * задаёт его и по «Убрать и отметить» снимает все планы дня одним запросом.
 */
export function TripRemoveModal({
  plan,
  onDone,
  onCancel,
}: {
  plan: PlanItem;
  /** rest — отметили «Не бегаю»; иначе вернулся постоянный план. */
  onDone: (result: { rest: boolean }) => void;
  onCancel: () => void;
}) {
  const [busy, setBusy] = useState<"return" | "rest" | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Вопрос сервера (409): на дате ещё планы — убрать все и отметить «Не бегаю»?
  const [ask, setAsk] = useState<string | null>(null);
  // Вопрос пришёл — кнопки подвала сменились: фокус на подтверждение, а не на <body>.
  const askRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (ask) {
      askRef.current?.focus();
    }
  }, [ask]);
  const panelRef = useRemoveModalFocus(".plan-trip-actions .btn.primary");
  const home = plan.displaces?.location ?? null;
  const onCancelRef = useRef(onCancel);
  onCancelRef.current = onCancel;
  const busyRef = useRef(busy);
  busyRef.current = busy;
  // DetailModal держит onClose в зависимостях эффекта блокировки прокрутки:
  // функция должна быть одна на всё время окна.
  const close = useCallback(() => {
    if (!busyRef.current) {
      onCancelRef.current();
    }
  }, []);

  useEffect(() => {
    setError(null);
    setAsk(null);
  }, [plan.ref]);

  const run = async (rest: boolean) => {
    if (!plan.id || busy) {
      return;
    }
    setBusy(rest ? "rest" : "return");
    setError(null);
    try {
      await deletePlan(plan.id, { rest });
      setBusy(null);
      onDone({ rest });
    } catch (err) {
      setBusy(null);
      // 404 — плана уже нет: суббота и так вернулась.
      if (err instanceof ApiError && err.status === 404) {
        onDone({ rest: false });
        return;
      }
      if (rest && err instanceof ApiError && err.status === 409) {
        setAsk(err.message);
        return;
      }
      setError(errorText(err, "Не удалось убрать план"));
    }
  };

  // «Убрать и отметить»: все планы дня уходят вместе с этим — одним запросом.
  const restAll = async () => {
    if (busy) {
      return;
    }
    setBusy("rest");
    setError(null);
    try {
      await saveDayRest(plan.plan_date, true);
      setBusy(null);
      onDone({ rest: true });
    } catch (err) {
      setBusy(null);
      setError(errorText(err, "Не удалось отметить «Не бегаю»"));
    }
  };

  const day = planDayLabel(plan.plan_date);
  const footer = (
    <div className="plan-modal-footer">
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {ask ? (
        <div className="plan-modal-actions plan-trip-actions">
          <button type="button" className="btn btn-ghost" disabled={busy !== null} onClick={() => setAsk(null)}>
            Назад
          </button>
          <button
            ref={askRef}
            type="button"
            className="btn primary"
            disabled={busy !== null}
            onClick={() => void restAll()}
          >
            {busy === "rest" && <span className="btn-spinner" aria-hidden="true" />}
            Убрать и отметить
          </button>
        </div>
      ) : (
        <div className="plan-modal-actions plan-trip-actions">
          <button type="button" className="btn btn-ghost" disabled={busy !== null} onClick={close}>
            Отмена
          </button>
          <button type="button" className="btn secondary" disabled={busy !== null} onClick={() => void run(true)}>
            {busy === "rest" && <span className="btn-spinner" aria-hidden="true" />}
            Не бегаю в этот день
          </button>
          <button type="button" className="btn primary" disabled={busy !== null} onClick={() => void run(false)}>
            {busy === "return" && <span className="btn-spinner" aria-hidden="true" />}
            {home ? `Вернуть: ${home.name}` : "Убрать план"}
          </button>
        </div>
      )}
    </div>
  );

  return (
    <DetailModal
      open
      title="Убрать план?"
      width="narrow"
      onClose={close}
      footer={footer}
      panelRef={panelRef}
      hideFromWebvisor
    >
      <div className="plan-modal plan-trip">
        <p>
          <strong>{day}</strong> — {plan.location?.name ?? "локация"}.
        </p>
        {ask ? (
          <p role="alert">{ask}</p>
        ) : (
          home && (
            <p>
              На эту дату вернётся ваш постоянный план — <strong>{home.name}</strong>. Не пойдёте и туда — отметьте «Не
              бегаю в этот день».
            </p>
          )
        )}
      </div>
    </DetailModal>
  );
}
