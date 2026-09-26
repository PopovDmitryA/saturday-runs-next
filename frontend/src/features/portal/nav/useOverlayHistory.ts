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
 * - переход по любой ссылке сайта, пока окно открыто, — сначала снимаем
 *   запись, потом переходим: иначе после перехода «Назад» один раз вёл бы
 *   «никуда». Ловим клики на всём документе, а не только внутри окна: из-под
 *   выпадающего списка полосы оставались нажимаемыми логотип, нижняя панель и
 *   переключатель локации организатора, и их переходы оставляли заглушку в
 *   истории (проверка 26.09.2026).
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
 * две заглушки подряд. Хук годится для любого окна сайта — им пользуется и
 * поиск.
 */
import { useCallback, useEffect, useRef, type MouseEvent as ReactMouseEvent } from "react";
import { normalizeAppPath } from "../../../hooks/useAppPath";
import { onEntryChange } from "../../../lib/historyEntry";
import { scrollForNavigation, stopScrollRestore } from "../../../lib/scrollMemory";

const OVERLAY_FIELD = "srsOverlay";
/** Поле с ключом записи — его кладёт обёртка lib/historyEntry. */
const ENTRY_FIELD = "srsEntry";
/** Столько ждём popstate после history.back(), потом закрываем окно сами. */
const BACK_FALLBACK_MS = 600;
/**
 * Метка на <html>, пока открыто любое окно: по ней CSS прячет то, что лежит
 * выше окон по z-index, — кнопку «Наверх страницы» (siteNavMobile.css).
 */
const OPEN_CLASS = "site-overlay-open";

type Holder = { token: string; release: () => void };

/** Окно, чья запись сейчас лежит на вершине истории. */
let holder: Holder | null = null;
let sequence = 0;
/** Сколько окон открыто — для метки на <html>. */
let openCount = 0;

function overlayTokenOf(state: unknown): string | null {
  if (state && typeof state === "object") {
    const value = (state as Record<string, unknown>)[OVERLAY_FIELD];
    if (typeof value === "string" && value) {
      return value;
    }
  }
  return null;
}

function entryKeyOf(state: unknown): string | null {
  if (state && typeof state === "object") {
    const value = (state as Record<string, unknown>)[ENTRY_FIELD];
    if (typeof value === "string" && value) {
      return value;
    }
  }
  return null;
}

/**
 * Заглушка, у которой нет открытого окна: вкладку обновили, пока было открыто
 * «Меню» (F5 окно закрывает, а запись остаётся), или окно закрыли и нажали
 * «Вперёд». Страница выглядит обычной, а под заглушкой лежит запись этой же
 * страницы с тем же ключом — и следующее «Назад» ничего видимого не делало
 * (проверка 26.09.2026). Здесь — ключ страницы, на заглушке которой мы стоим.
 */
let ghost: { key: string | null } | null = null;

function ghostOf(state: unknown): { key: string | null } | null {
  const token = overlayTokenOf(state);
  if (token === null || holder?.token === token) return null;
  return { key: entryKeyOf(state) };
}

if (typeof window !== "undefined") {
  // После перезагрузки history.state приезжает прежний — с меткой окна.
  ghost = ghostOf(window.history.state);
  window.addEventListener("popstate", (event) => {
    const left = ghost;
    ghost = ghostOf(event.state);
    // Ушли «Назад» с заглушки на запись этой же страницы — человек этого шага
    // не видел. Делаем за него ещё один, настоящий: одно нажатие — один шаг.
    // Снимок это не трогает: обе записи — одна страница с одним ключом, она не
    // пересоздаётся. А докрутку этой страницы (после F5 она ещё может идти)
    // гасим: страница уходит, и докрутка утащила бы прошлую на свою позицию
    // (NAV-2, см. lib/scrollMemory).
    if (left && overlayTokenOf(event.state) === null && entryKeyOf(event.state) === left.key) {
      stopScrollRestore();
      window.history.back();
    }
  });
  // Новый переход вперёд: мы больше не на заглушке.
  onEntryChange(({ reason }) => {
    if (reason === "push") ghost = null;
  });
}

function pushOverlayEntry(token: string): void {
  const current = window.history.state;
  const base = current && typeof current === "object" ? (current as Record<string, unknown>) : {};
  // History.prototype — исходный метод: обёртка historyEntry висит на самом
  // объекте window.history. Адрес не передаём — запись на тот же адрес.
  History.prototype.pushState.call(window.history, { ...base, [OVERLAY_FIELD]: token }, "");
  // Если открыли окно, стоя на заглушке, новая запись — живая.
  ghost = null;
}

type InAppTarget = { url: URL; full: boolean };

/**
 * Куда ведёт клик, если это обычный переход внутри сайта — те же условия, что
 * у перехватчика ссылок в hooks/useAppPath. null — клик не наш (новая
 * вкладка, внешний адрес, якорь на той же странице).
 */
function inAppTarget(event: MouseEvent | ReactMouseEvent): InAppTarget | null {
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

function go({ url, full }: InAppTarget): void {
  if (full) {
    window.location.assign(url.href);
  } else {
    navigateInApp(url);
  }
}

export type OverlayHistory = {
  /** Закрыть окно: снять его запись из истории (окно закроет popstate). */
  dismiss: () => void;
  /** Закрыть окно, а потом сделать следующее действие (открыть поиск и т.п.). */
  dismissThen: (after: () => void) => void;
  /**
   * Обработчик onClick для ссылок окна. Переходы, пока окно открыто, хук и так
   * ловит на всём документе; этот обработчик — для кода, которому нужно
   * сделать что-то своё до перехода (поиск пишет клик в журнал).
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
    openCount += 1;
    document.documentElement.classList.add(OPEN_CLASS);

    const onPop = () => {
      const mine = tokenRef.current;
      if (mine === null || overlayTokenOf(window.history.state) === mine) return;
      // Запись окна сняли — «Назад» или наш же history.back().
      finish();
    };

    // Любая ссылка сайта, пока окно открыто, — сначала закрыть окно (снять
    // запись), потом перейти. Фаза захвата на документе — раньше роутера
    // (hooks/useAppPath) и раньше обработчиков React: роутер увидит
    // defaultPrevented и переход не повторит. Сам переход — после того как
    // клик отработает целиком: обработчики самой ссылки (журнал поиска) успеют
    // записать своё до закрытия окна.
    const onClickCapture = (event: MouseEvent) => {
      if (tokenRef.current === null) return;
      const target = inAppTarget(event);
      if (!target) return;
      event.preventDefault();
      window.setTimeout(() => {
        if (tokenRef.current === null) {
          // Окно успело закрыться само (Esc, «Назад») — просто переходим.
          go(target);
          return;
        }
        afterRef.current = () => go(target);
        dismiss();
      }, 0);
    };

    window.addEventListener("popstate", onPop);
    document.addEventListener("click", onClickCapture, true);
    return () => {
      window.removeEventListener("popstate", onPop);
      document.removeEventListener("click", onClickCapture, true);
      openCount = Math.max(0, openCount - 1);
      if (openCount === 0) document.documentElement.classList.remove(OPEN_CLASS);
      if (holder === self) holder = null;
      const mine = tokenRef.current;
      tokenRef.current = null;
      // Окно закрыли мимо dismiss (состояние сменили снаружи), а его запись
      // всё ещё на вершине — снимаем, иначе «Назад» один раз «не сработает».
      if (mine !== null && overlayTokenOf(window.history.state) === mine) {
        window.history.back();
      }
    };
  }, [open, finish, dismiss]);

  const interceptLinks = useCallback(
    (event: ReactMouseEvent) => {
      if (tokenRef.current === null) return;
      // Обычно клик уже перехвачен на документе (defaultPrevented) — тогда
      // здесь делать нечего.
      const target = inAppTarget(event);
      if (!target) return;
      // Роутер (hooks/useAppPath) пропускает клики с defaultPrevented — переход
      // сделаем сами, когда запись окна уже снята.
      event.preventDefault();
      dismissThen(() => go(target));
    },
    [dismissThen],
  );

  useEffect(() => () => window.clearTimeout(timerRef.current), []);

  return { dismiss, dismissThen, interceptLinks };
}
