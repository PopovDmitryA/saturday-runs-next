import { useEffect } from "react";
import { getOrCreateVisitorId } from "./siteVisitor";

/**
 * События сайта в таблице ab_events.
 *
 * Исторически здесь жил АБ-тест главной (эксперимент home_v1, 27.07–22.08.2026).
 * Тест завершён: вариант B выиграл по конверсии в регистрацию (22.5% против
 * 15.0% на зрителя главной, p=0.012) и принят как единственная главная.
 * Раскладки 50/50, форса ?ab= и поэлементной инструментовки эксперимента
 * больше нет — вместо них постоянный счётчик воронки.
 *
 * Три живых потребителя:
 * - воронка регистрации (канал "funnel") — useFunnelHomeView/trackCtaClick/
 *   trackAuthStart/reportAuthDoneOnce, сводка в админке «Популярность»;
 * - trackHomeLinkClick — «куда уводит главная» (канал "home_v1");
 * - abVisitorKey — общий ключ посетителя, им же пользуется фича «Поделиться».
 */
const FUNNEL_CHANNEL = "funnel";
const HOME_CHANNEL = "home_v1";

// Вариантов больше нет: пишем "-", как канал "share". Заодно в SQL видно, где
// кончился эксперимент главной.
const NO_VARIANT = "-";

/**
 * Ключ посетителя — ВСЕГДА анонимный ("a:<id>"), в отличие от buildVisitorKey
 * общей аналитики, где после логина ключ меняется на "u:<user_id>". Только так
 * ступени воронки «увидел главную → кликнул → вошёл» сшиваются в одну цепочку
 * сквозь редирект на VK/Яндекс; самого пользователя сервер видит по куке.
 */
export function abVisitorKey(): string {
  return `a:${getOrCreateVisitorId()}`;
}

function sendEvent(channel: string, eventType: string, value: string): void {
  const body = JSON.stringify({
    experiment: channel,
    variant: NO_VARIANT,
    visitor_key: abVisitorKey(),
    event_type: eventType,
    value,
    path: window.location.pathname,
  });
  try {
    // sendBeacon: не блокирует UI и доживает до закрытия вкладки.
    navigator.sendBeacon("/api/stats/event", new Blob([body], { type: "application/json" }));
  } catch {
    // ignore analytics errors
  }
}

/**
 * Ступень 1 — главная открыта. Знаменатель всей воронки, поэтому шлём сразу
 * при отрисовке, не дожидаясь загрузки данных: иначе в выборку попадут только
 * те, у кого страница успела дорисоваться, и конверсия окажется завышенной.
 */
export function useFunnelHomeView(): void {
  useEffect(() => {
    sendEvent(FUNNEL_CHANNEL, "home_view", "");
    // Пустые зависимости: строго один раз за монтирование страницы.
  }, []);
}

/** Ступень 2 — клик по кнопке входа. place: "hero" | "bottom" | "teaser". */
export function trackCtaClick(place: string): void {
  sendEvent(FUNNEL_CHANNEL, "cta_click", place);
}

/** Ступень 3 — человек выбрал провайдера и уходит на его страницу входа. */
export function trackAuthStart(provider: string): void {
  sendEvent(FUNNEL_CHANNEL, "auth_start", provider);
}

/**
 * Ступень 4 — вход завершён. Шлётся один раз на пару (браузер, пользователь):
 * иначе каждое открытие сайта с живой сессией засчитывалось бы как новый вход
 * и знаменатель воронки поплыл бы. Когорту new (регистрация) или returning
 * (вернулся) определяет сервер по возрасту аккаунта.
 *
 * Пятой ступени — привязки платформы — здесь нет: она считается по
 * platform_links, у события не было бы своего источника правды.
 */
export function reportAuthDoneOnce(userId: string): void {
  try {
    const key = `sr_funnel_auth_done:${userId}`;
    if (localStorage.getItem(key) !== null) {
      return;
    }
    localStorage.setItem(key, "1");
  } catch {
    return;
  }
  sendEvent(FUNNEL_CHANNEL, "auth_done", "");
}

/**
 * Переход по внутренней ссылке главной: имя локации → /locations/{slug},
 * имя участника → /users/{хендл}. Отвечает на вопрос «уводит ли главная людей
 * вглубь сайта и куда именно». Сводка — в админке «Популярность».
 */
export function trackHomeLinkClick(kind: "location" | "runner", target: string): void {
  sendEvent(HOME_CHANNEL, "home_link_click", `${kind}:${target}`);
}
