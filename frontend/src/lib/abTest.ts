import { useEffect, type RefObject } from "react";
import { getOrCreateVisitorId } from "./siteVisitor";

/**
 * АБ-эксперименты главной страницы.
 *
 * Пилот включается переменной окружения VITE_AB_HOME_ACTIVE=true (прод-.env на
 * сервере, см. .env.example). Выключен — все видят вариант A, события пишутся
 * с variant="A" как базовая линия «до». Включён — посетители детерминированно
 * разложатся 50/50 по стабильному анонимному id браузера (перезагрузка
 * страницы и VK-редирект вариант не меняют).
 *
 * ВАЖНО: переменная читается ВО ВРЕМЯ СБОРКИ фронта (Vite зашивает значение в
 * бандл), поэтому включение и выключение пилота требуют пересборки — то есть
 * деплоя. Мгновенного рубильника без деплоя здесь нет.
 */
export const HOME_EXPERIMENT = "home_v1";

const HOME_EXPERIMENT_ACTIVE = import.meta.env.VITE_AB_HOME_ACTIVE === "true";

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
 * Переход по внутренней ссылке главной: имя локации → /locations/{slug},
 * имя участника → /users/{хендл}.
 *
 * Событие одно на оба варианта эксперимента (ссылки есть и в A, и в B) —
 * кроме сравнения A/B оно отвечает на вопрос «уводит ли главная людей вглубь
 * сайта и куда именно». Сводка — в админке «Популярность».
 */
export function trackHomeLinkClick(kind: "location" | "runner", target: string): void {
  trackAbEvent("home_link_click", `${kind}:${target}`);
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

/**
 * Показ варианта главной — один раз за просмотр страницы.
 *
 * Это знаменатель эксперимента: клики и логины без него — абсолютные числа,
 * зависящие от того, кому сколько раз показали, и сравнивать по ним A с B
 * нельзя. Отправляем сразу при отрисовке, не дожидаясь скролла: иначе в
 * выборку попадут только те, кто долистал.
 */
export function useAbVariantView(): void {
  useEffect(() => {
    trackAbEvent("variant_view");
    // Пустые зависимости: строго один раз за монтирование страницы.
  }, []);
}
