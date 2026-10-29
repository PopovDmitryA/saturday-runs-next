import { isSessionMissingError } from "../../lib/api";
import { resumeSeries, type PlanSeries } from "./plannerApi";
import { formatDayMonth, formatDayMonthShort, parseIsoDay, weekdayMonFirst } from "./plannerDates";

// Лёгкие подписи и проверки постоянного плана («каждую субботу») — без
// стилей и окон: их берут и места из стартового чанка («Обзор», шапка
// профиля). Окна и подсказка — в seriesParts.

/** Значок постоянного плана — везде один. */
export const SERIES_ICON = "↻";

export function errorText(err: unknown, fallback: string): string {
  if (isSessionMissingError(err)) {
    return "Сессия закончилась — войдите снова";
  }
  return err instanceof Error && err.message ? err.message : fallback;
}

/** Суббота ли дата (особая суббота — тоже суббота: по дню недели, а не по виду даты). */
export function isSaturday(iso: string): boolean {
  return weekdayMonFirst(parseIsoDay(iso)) === 5;
}

/**
 * «с 31 октября», «с 31 октября до 26 декабря». Начался давно — «с …» всё
 * равно пишем: так человек видит, с какой субботы план в силе.
 */
export function seriesSpanText(series: Pick<PlanSeries, "starts_on" | "until">): string {
  const from = `с ${formatDayMonth(series.starts_on)}`;
  return series.until ? `${from} до ${formatDayMonth(series.until)}` : from;
}

/**
 * Встанет ли дата в постоянный план — для подсказок окна плана до сохранения.
 * Приближение правила сервера: суббота в сроке, без «Не бегаю» и поездки,
 * план не на паузе. Суббота входит всегда, особая дата из админки — тоже
 * (решение Дмитрия 26.10.2026: «особые даты всегда проставляются вручную,
 * не надо никакой логики»). Точный ответ после сохранения дают поля ответа
 * (displaced).
 */
export function seriesCoversDate(
  series: PlanSeries,
  iso: string,
  options: { special?: boolean; ignoreAway?: boolean } = {},
): boolean {
  if (series.sleeping || !series.plannable || iso < series.starts_on) {
    return false;
  }
  if (series.until && iso > series.until) {
    return false;
  }
  if (series.skipped.includes(iso)) {
    return false;
  }
  if (!options.ignoreAway && series.away.some((item) => item.date === iso)) {
    return false;
  }
  return isSaturday(iso) || Boolean(options.special);
}

/** «21 ноя, 28 ноя» — короткие даты списком. */
export function shortDays(dates: string[]): string {
  return dates.map((iso) => formatDayMonthShort(iso)).join(", ");
}

/** «план на паузе» / «каждую субботу до 26 декабря» — короткий статус для строк. */
export function seriesStatusText(series: PlanSeries): string {
  if (series.sleeping) {
    return "на паузе";
  }
  if (series.expired) {
    return series.until ? `закончился ${formatDayMonth(series.until)}` : "закончился";
  }
  if (!series.plannable) {
    return "локация сейчас не проводит старты";
  }
  return series.until ? `каждую субботу до ${formatDayMonth(series.until)}` : "каждую субботу";
}

// Срок плана (его ставило прежнее окно) подходит к концу — за две недели до
// последней субботы на карточке появляется «Продлить» (и держится, пока план
// не остановился сам).
const RENEW_BEFORE_DAYS = 14;

/** Пора ли предлагать «Продлить» срок плана. */
export function seriesNeedsRenewal(series: PlanSeries, todayIso: string): boolean {
  if (!series.until) {
    return false;
  }
  if (series.expired) {
    return true;
  }
  const days = Math.round((parseIsoDay(series.until).getTime() - parseIsoDay(todayIso).getTime()) / 86_400_000);
  return days <= RENEW_BEFORE_DAYS;
}

/** «Продолжить» план на паузе — общий вызов для блока, «Планов» и «Обзора». */
export async function resumeSeriesQuietly(onChanged: () => void, onError: (text: string) => void) {
  try {
    await resumeSeries();
    onChanged();
  } catch (err) {
    onError(errorText(err, "Не удалось продолжить постоянный план"));
  }
}
