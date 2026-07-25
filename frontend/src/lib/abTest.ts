import { useEffect, type RefObject } from "react";
import { getOrCreateVisitorId } from "./siteVisitor";

/**
 * АБ-эксперименты главной страницы.
 *
 * Пока собран только вариант A (текущая главная), эксперимент выключен:
 * все посетители видят A, и события пишутся с variant="A" — это базовая
 * линия «до». Когда вариант B будет готов, переключить
 * HOME_EXPERIMENT_ACTIVE в true — посетители детерминированно разложатся
 * 50/50 по стабильному анонимному id браузера (перезагрузка страницы и
 * VK-редирект вариант не меняют).
 */
export const HOME_EXPERIMENT = "home_v1";

const HOME_EXPERIMENT_ACTIVE = false;

export type AbVariant = "A" | "B";

/** FNV-1a: стабильный бакет из анонимного id браузера. */
function bucketOf(id: string): AbVariant {
  let hash = 0x811c9dc5;
  for (let i = 0; i < id.length; i += 1) {
    hash ^= id.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0) % 2 === 0 ? "A" : "B";
}

/**
 * Принудительный вариант для просмотра глазами: ?ab=B (или ?ab=A) в адресе.
 * Нужен, чтобы посмотреть вариант B до запуска эксперимента; такие просмотры
 * не пишутся в ab_events — QA не должен загрязнять данные.
 */
function forcedVariant(): AbVariant | null {
  try {
    const param = new URLSearchParams(window.location.search).get("ab");
    if (param === "A" || param === "B") {
      return param;
    }
  } catch {
    // ignore
  }
  return null;
}

export function getHomeVariant(): AbVariant {
  const forced = forcedVariant();
  if (forced !== null) {
    return forced;
  }
  if (!HOME_EXPERIMENT_ACTIVE) {
    return "A";
  }
  return bucketOf(getOrCreateVisitorId());
}

/**
 * Ключ посетителя для АБ-событий — ВСЕГДА анонимный ("a:<id>"), в отличие от
 * buildVisitorKey общей аналитики, где после логина ключ меняется на
 * "u:<user_id>". Иначе воронку «увидел главную → кликнул CTA → залогинился»
 * нельзя сшить сквозь VK-редирект; сам пользователь виден серверу по куке.
 */
export function abVisitorKey(): string {
  return `a:${getOrCreateVisitorId()}`;
}

export function trackAbEvent(eventType: string, value = ""): void {
  if (forcedVariant() !== null) {
    return;
  }
  const body = JSON.stringify({
    experiment: HOME_EXPERIMENT,
    variant: getHomeVariant(),
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
 * Глубина скролла страницы: события 25/50/75/100 (%), каждое один раз за
 * просмотр. ready передавать только когда контент загружен — до этого высота
 * страницы «пустая» и проценты врут.
 */
export function useAbScrollDepth(ready: boolean): void {
  useEffect(() => {
    if (!ready) {
      return;
    }
    const sent = new Set<number>();
    const onScroll = () => {
      const total = document.documentElement.scrollHeight - window.innerHeight;
      if (total <= 0) {
        return;
      }
      const pct = (window.scrollY / total) * 100;
      for (const threshold of [25, 50, 75, 100]) {
        if (pct >= threshold - 0.5 && !sent.has(threshold)) {
          sent.add(threshold);
          trackAbEvent("scroll_depth", String(threshold));
        }
      }
      if (sent.size === 4) {
        window.removeEventListener("scroll", onScroll);
      }
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => window.removeEventListener("scroll", onScroll);
  }, [ready]);
}

/**
 * CTA попала во вьюпорт — один раз за просмотр. Знаменатель для честного CTR
 * кнопки: клики делить на «кто кнопку видел», а не на все визиты.
 */
export function useAbCtaView(ref: RefObject<Element | null>, placement: string, ready: boolean): void {
  useEffect(() => {
    const el = ref.current;
    if (!ready || el === null || typeof IntersectionObserver === "undefined") {
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        trackAbEvent("cta_view", placement);
        observer.disconnect();
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, placement, ready]);
}

/**
 * Завершённый вход: шлётся один раз на пару (браузер, пользователь).
 * Когорту new/returning сервер определяет сам по возрасту аккаунта —
 * вернувшиеся разлогиненные участники в конверсию эксперимента не идут.
 */
export function reportAbLoginOnce(userId: string): void {
  try {
    const key = `sr_ab_login_sent:${userId}`;
    if (localStorage.getItem(key) !== null) {
      return;
    }
    localStorage.setItem(key, "1");
  } catch {
    return;
  }
  trackAbEvent("login_complete");
}
