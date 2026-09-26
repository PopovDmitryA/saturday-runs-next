/**
 * «Назад» закрывает окно поверх страницы, а не уводит со страницы.
 *
 * На Android системная «Назад» — главный способ закрыть всё, что открылось:
 * «Меню», список страниц раздела, поиск. Раньше эти окна в историю браузера
 * ничего не писали, и жест уводил на предыдущую страницу вместе с прокруткой
 * и фильтрами, а окно порой оставалось висеть поверх чужой страницы
 * (ревью навигации 25.09.2026, mob-1 и code-4).
 *
 * Как устроено:
 * - открыли окно — кладём в историю запись-«заглушку» на тот же адрес;
 * - «Назад» снимает её, приходит popstate — закрываем окно, страница на месте;
 * - закрыли окно сами (кнопка, тап мимо, Esc) — снимаем свою запись через
 *   history.back(), чтобы в истории не осталось «пустого» шага;
 * - переход по ссылке из окна — сначала снимаем запись, потом переходим:
 *   иначе после перехода «Назад» один раз вёл бы «никуда».
 *
 * Главная тонкость — ключ записи. Роутер пересобирает страницу при смене ключа
 * записи истории (App: Fragment key=entryKey, см. lib/historyEntry), а обёртка
 * над pushState выдаёт каждой новой записи новый ключ. Если бы окно писало в
 * историю через неё, страница под окном пересоздалась бы и потеряла состояние.
 * Поэтому запись окна кладём «сырым» pushState мимо обёртки и копируем в неё
 * ключ страницы: при «Назад» ключ не меняется — страница та же, прокрутка та же.
 *
 * Одновременно открыто не больше одного окна: новое окно забирает запись у
 * предыдущего (оно закрывается без «назад»), так в истории никогда не копятся
 * две заглушки подряд. Хук годится для любого окна сайта — им может
 * пользоваться и поиск.
 */
import { useCallback, useEffect, useRef, type MouseEvent as ReactMouseEvent } from "react";
import { normalizeAppPath } from "../../../hooks/useAppPath";
import { scrollForNavigation } from "../../../lib/scrollMemory";

const OVERLAY_FIELD = "srsOverlay";
/** Столько ждём popstate после history.back(), потом закрываем окно сами. */
const BACK_FALLBACK_MS = 600;

type Holder = { token: string; release: () => void };

/** Окно, чья запись сейчас лежит на вершине истории. */
let holder: Holder | null = null;
let sequence = 0;

function overlayTokenOf(state: unknown): string | null {
  if (state && typeof state === "object") {
    const value = (state as Record<string, unknown>)[OVERLAY_FIELD];
    if (typeof value === "string" && value) {
      return value;
    }
  }
  return null;
}

function pushOverlayEntry(token: string): void {
  const current = window.history.state;
  const base = current && typeof current === "object" ? (current as Record<string, unknown>) : {};
  // History.prototype — исходный метод: обёртка historyEntry висит на самом
  // объекте window.history. Адрес не передаём — запись на тот же адрес.
  History.prototype.pushState.call(window.history, { ...base, [OVERLAY_FIELD]: token }, "");
}

/**
 * Куда ведёт клик, если это обычный переход внутри сайта — те же условия, что
 * у перехватчика ссылок в hooks/useAppPath. null — клик не наш (новая
 * вкладка, внешний адрес, якорь на той же странице).
 */
function inAppTarget(event: ReactMouseEvent): { url: URL; full: boolean } | null {
  if (event.defaultPrevented || event.button !== 0) return null;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return null;
  const anchor = (event.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
  if (!anchor || anchor.target === "_blank" || anchor.hasAttribute("download")) return null;
  const href = anchor.getAttribute("href");
  if (!href || href.startsWith("#") || href.startsWith("mailto:") || href.startsWith("tel:")) return null;
  const url = new URL(href, window.location.href);
  if (url.origin !== window.location.origin) return null;
  // /api/… и data-full-nav роутер отдаёт браузеру целиком (OAuth, выгрузки).
  const full = url.pathname.startsWith("/api/") || anchor.hasAttribute("data-full-nav");
  return { url, full };
}

/** Переход без перезагрузки — ровно как у ссылок сайта (hooks/useAppPath). */
function navigateInApp(url: URL): void {
  const pathChanged = normalizeAppPath(url.pathname) !== normalizeAppPath();
  if (!pathChanged && url.search === window.location.search) {
    // Ссылка на открытую страницу: окно уже закрыто — этого человек и хотел.
    if (url.hash) scrollForNavigation(url.hash);
    return;
  }
  window.history.pushState(null, "", `${url.pathname}${url.search}${url.hash}`);
  if (pathChanged) {
    scrollForNavigation(url.hash);
  }
}

export type OverlayHistory = {
  /** Закрыть окно: снять его запись из истории (окно закроет popstate). */
  dismiss: () => void;
  /** Закрыть окно, а потом сделать следующее действие (открыть поиск и т.п.). */
  dismissThen: (after: () => void) => void;
  /**
   * Обработчик onClick для контейнеров окна: переход по ссылке внутри окна
   * сначала снимает запись окна, потом переходит.
   */
  interceptLinks: (event: ReactMouseEvent) => void;
};

/**
 * @param open открыто ли окно сейчас;
 * @param onClose закрыть окно в состоянии компонента (без истории — ею
 *   занимается хук).
 */
export function useOverlayHistory(open: boolean, onClose: () => void): OverlayHistory {
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  });
  const tokenRef = useRef<string | null>(null);
  const afterRef = useRef<(() => void) | null>(null);
  const timerRef = useRef(0);

  // Окно закрыто, его записи в истории больше нет.
  const finish = useCallback(() => {
    window.clearTimeout(timerRef.current);
    tokenRef.current = null;
    closeRef.current();
    const after = afterRef.current;
    afterRef.current = null;
    after?.();
  }, []);

  useEffect(() => {
    if (!open) return;
    let token: string;
    if (holder && overlayTokenOf(window.history.state) === holder.token) {
      // Другое окно ещё держит запись на вершине истории — забираем её себе,
      // а его закрываем без «назад»: окно вместо окна — та же одна запись.
      token = holder.token;
      holder.release();
    } else {
      sequence += 1;
      token = `ov-${Date.now().toString(36)}-${sequence}`;
      pushOverlayEntry(token);
    }
    tokenRef.current = token;
    const self: Holder = {
      token,
      release: () => {
        tokenRef.current = null;
        afterRef.current = null;
        closeRef.current();
      },
    };
    holder = self;

    const onPop = () => {
      const mine = tokenRef.current;
      if (mine === null || overlayTokenOf(window.history.state) === mine) return;
      // Запись окна сняли — «Назад» или наш же history.back().
      finish();
    };
    window.addEventListener("popstate", onPop);
    return () => {
      window.removeEventListener("popstate", onPop);
      if (holder === self) holder = null;
      const mine = tokenRef.current;
      tokenRef.current = null;
      // Окно закрыли мимо dismiss (состояние сменили снаружи), а его запись
      // всё ещё на вершине — снимаем, иначе «Назад» один раз «не сработает».
      if (mine !== null && overlayTokenOf(window.history.state) === mine) {
        window.history.back();
      }
    };
  }, [open, finish]);

  const dismiss = useCallback(() => {
    const token = tokenRef.current;
    if (token !== null && overlayTokenOf(window.history.state) === token) {
      window.history.back();
      // Страховка: браузер не прислал popstate — закрываем сами.
      window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => {
        if (tokenRef.current === token) finish();
      }, BACK_FALLBACK_MS);
      return;
    }
    finish();
  }, [finish]);

  const dismissThen = useCallback(
    (after: () => void) => {
      afterRef.current = after;
      dismiss();
    },
    [dismiss],
  );

  const interceptLinks = useCallback(
    (event: ReactMouseEvent) => {
      if (tokenRef.current === null) return;
      const target = inAppTarget(event);
      if (!target) return;
      // Роутер (hooks/useAppPath) пропускает клики с defaultPrevented — переход
      // сделаем сами, когда запись окна уже снята.
      event.preventDefault();
      const { url, full } = target;
      dismissThen(() => {
        if (full) {
          window.location.assign(url.href);
        } else {
          navigateInApp(url);
        }
      });
    },
    [dismissThen],
  );

  useEffect(() => () => window.clearTimeout(timerRef.current), []);

  return { dismiss, dismissThen, interceptLinks };
}
