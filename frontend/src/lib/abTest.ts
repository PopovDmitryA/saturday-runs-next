import { getOrCreateVisitorId } from "./siteVisitor";

/**
 * События главной страницы в таблице ab_events.
 *
 * Исторически здесь жил АБ-тест главной (эксперимент home_v1, 27.07–22.08.2026).
 * Тест завершён: вариант B выиграл по конверсии в регистрацию (22.5% против
 * 15.0% на зрителя главной, p=0.012) и принят как единственная главная —
 * раскладки 50/50, форса ?ab= и инструментовки эксперимента (variant_view,
 * scroll_depth, cta_view/cta_click, period, chart_tab, login_complete) больше
 * нет. Сырые события теста остаются в ab_events, сводка показов — в админке
 * «Популярность».
 *
 * Осталось два живых потребителя:
 * - trackHomeLinkClick — «куда уводит главная» (отчёт «Переходы с главной»);
 * - abVisitorKey — общий ключ посетителя, им же пользуется фича «Поделиться».
 */
const HOME_EXPERIMENT = "home_v1";

// Вариант в схеме события обязателен, а вариантов больше нет: пишем "-", как
// это делает канал "share". Заодно в SQL видно, где кончился эксперимент.
const NO_VARIANT = "-";

/**
 * Ключ посетителя — ВСЕГДА анонимный ("a:<id>"), в отличие от buildVisitorKey
 * общей аналитики, где после логина ключ меняется на "u:<user_id>". Так
 * действия одного человека сшиваются в цепочку сквозь VK-редирект; сам
 * пользователь виден серверу по куке.
 */
export function abVisitorKey(): string {
  return `a:${getOrCreateVisitorId()}`;
}

function trackHomeEvent(eventType: string, value: string): void {
  const body = JSON.stringify({
    experiment: HOME_EXPERIMENT,
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
 * Переход по внутренней ссылке главной: имя локации → /locations/{slug},
 * имя участника → /users/{хендл}. Отвечает на вопрос «уводит ли главная людей
 * вглубь сайта и куда именно». Сводка — в админке «Популярность».
 */
export function trackHomeLinkClick(kind: "location" | "runner", target: string): void {
  trackHomeEvent("home_link_click", `${kind}:${target}`);
}
