import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ConfirmModal } from "../../components/ConfirmModal";
import { ApiError, isSessionMissingError } from "../../lib/api";
import { clearDayRest, saveDayRest, type PlanItem } from "./plannerApi";
import { planDayLabel } from "./plannerDates";

/**
 * «Не бегаю» на день старта — личный планер (решение Дмитрия 26.10.2026:
 * «прямо очевидно отметить — не бегаю»). На локации и в уведомлениях её нет,
 * напоминаний на этот день не будет. Публичная отметка (по умолчанию — как
 * профиль; решение 27.10.2026) видна в календаре профиля как «не бегает»,
 * переключают её в деталях дня «Моих планов».
 *
 * Общая механика для «Планов», блока локации и карточки на «Обзоре»: на дне
 * с планами пробежки сначала спрашиваем, убрать ли их (сервер без replace
 * ответил бы 409). Все планы дня знают только «Планы» и передают их; блок
 * локации и «Обзор» видят не все (на особую дату их до трёх) и передают
 * пустой список — тогда, как и при устаревших данных, сервер отвечает 409, и
 * спрашиваем тем же окном его готовым вопросом.
 */

type Pending = { date: string; question: string };

/** Чем кончилось последнее действие: на дне `date` отметка теперь `rest`. */
type Settled = { date: string; rest: boolean };

function errorText(err: unknown, fallback: string): string {
  if (isSessionMissingError(err)) {
    return "Сессия закончилась — войдите снова";
  }
  return err instanceof Error && err.message ? err.message : fallback;
}

/**
 * «На сб 31 октября у вас в планах: Зеленоград. Убрать план и отметить «Не
 * бегаю»?» — тот же вопрос, что сервер кладёт в ответ 409. Названия — в
 * именительном: «в Зеленограде» из произвольного названия не склонить.
 */
function restQuestion(date: string, plans: PlanItem[]): string {
  const names = plans.map((plan) => plan.location?.name ?? "другая локация").join(", ");
  const what = plans.length > 1 ? "планы" : "план";
  return `На ${planDayLabel(date)} у вас в планах: ${names}. Убрать ${what} и отметить «Не бегаю»?`;
}

export type DayRestControls = {
  /**
   * Отметить «Не бегаю»; plans — ВСЕ планы пробежки этого дня (вхождения
   * постоянного плана можно передавать — их окно не спрашивает). Не знаете
   * всех — пустой список: вопрос задаст сервер, а не окно про часть планов.
   */
  markRest: (date: string, plans: PlanItem[]) => void;
  /** Снять отметку. */
  clearRest: (date: string) => void;
  busy: boolean;
  error: string | null;
  /** Окно подтверждения — положить в разметку страницы. */
  confirm: ReactNode;
  /** Последнее удачное действие — куда вернуть фокус (useRestFocus). */
  settled: Settled | null;
  clearSettled: () => void;
};

export function useDayRest(onChanged: () => void): DayRestControls {
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [settled, setSettled] = useState<Settled | null>(null);
  const clearSettled = useCallback(() => setSettled(null), []);

  const run = async (
    action: () => Promise<unknown>,
    fallback: string,
    date: string,
    replace: boolean,
    rest: boolean,
  ) => {
    setBusy(true);
    setError(null);
    setSettled(null);
    try {
      await action();
      setPending(null);
      setSettled({ date, rest });
      onChanged();
    } catch (err) {
      if (!replace && err instanceof ApiError && err.status === 409) {
        // Планы на этот день появились в другой вкладке: текст ошибки —
        // готовый вопрос сервера, спрашиваем им.
        setPending({ date, question: err.message });
        onChanged();
      } else {
        setPending(null);
        setError(errorText(err, fallback));
      }
    } finally {
      setBusy(false);
    }
  };

  const setRest = (date: string, replace: boolean) =>
    void run(() => saveDayRest(date, replace), "Не удалось отметить «Не бегаю»", date, replace, true);

  const markRest = (date: string, plans: PlanItem[]) => {
    setError(null);
    // Вхождение постоянного плана — не конфликт: «Не бегаю» на его дату и
    // есть «Пропущу». Спрашиваем только про настоящие строки планов.
    const rows = plans.filter((plan) => plan.id !== null);
    if (rows.length > 0) {
      setPending({ date, question: restQuestion(date, rows) });
      return;
    }
    setRest(date, false);
  };

  const clearRest = (date: string) =>
    void run(() => clearDayRest(date), "Не удалось снять отметку", date, true, false);

  const confirm = (
    <ConfirmModal
      open={pending !== null}
      title="Отметить «Не бегаю»?"
      confirmLabel="Убрать и отметить"
      cancelLabel="Оставить план"
      variant="danger"
      confirmLoading={busy}
      hideFromWebvisor
      onConfirm={() => {
        if (pending) {
          setRest(pending.date, true);
        }
      }}
      onCancel={() => setPending(null)}
    >
      {pending?.question ?? ""}
    </ConfirmModal>
  );

  return { markRest, clearRest, busy, error, confirm, settled, clearSettled };
}

/**
 * Фокус после «Не бегаю» и «Отменить». Нажатая кнопка уходит вместе с видом
 * дня, и фокус падал на <body>: клавиатура и читалка теряли место (ревью
 * 26.10.2026, UI-D4). Ref — на кнопку дня в обоих видах; когда данные
 * перечитаны и вид совпал с действием, фокус встаёт на ту, что пришла взамен.
 */
export function useRestFocus(controls: DayRestControls, date: string | null, rested: boolean) {
  const ref = useRef<HTMLButtonElement>(null);
  const { settled, clearSettled } = controls;
  useEffect(() => {
    if (settled && settled.date === date && settled.rest === rested && ref.current) {
      ref.current.focus();
      clearSettled();
    }
  }, [settled, date, rested, clearSettled]);
  return ref;
}
