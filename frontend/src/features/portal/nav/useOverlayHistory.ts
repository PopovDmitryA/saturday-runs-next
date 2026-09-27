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
 *
 * Подшаг (pushStep/popStep) — экран внутри окна, из которого «Назад» ведёт
 * обратно в окно, а не закрывает его: «Это вы?» в поиске (27.09.2026). Это
 * вторая запись с тем же токеном и полем шага. Системное «Назад» снимает её —
 * подшаг закрывается, окно остаётся; второе «Назад» закрывает окно. Закрыть
 * окно прямо с подшага (крестик, переход по ссылке) — снять обе записи разом
 * (history.go(-2)). У окон без подшагов всё как раньше: одна запись, один шаг.
 */
import { useCallback, useEffect, useRef, type MouseEvent as ReactMouseEvent } from "react";
import { normalizeAppPath } from "../../../hooks/useAppPath";
import { onEntryChange } from "../../../lib/historyEntry";
import { scrollForNavigation, stopScrollRestore } from "../../../lib/scrollMemory";

const OVERLAY_FIELD = "srsOverlay";
/** Поле записи подшага окна (см. pushStep). */
const STEP_FIELD = "srsOverlayStep";
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

function isStepEntry(state: unknown): boolean {
  return Boolean(state && typeof state === "object" && (state as Record<string, unknown>)[STEP_FIELD] === 1);
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
 * (проверка 26.09.2026). Здесь — ключ страницы, на заглушке которой мы стоим,
 * и не запись ли это подшага (под ней — ещё одна заглушка того же окна).
 */
type Ghost = { key: string | null; step: boolean };
let ghost: Ghost | null = null;

function ghostOf(state: unknown): Ghost | null {
  const token = overlayTokenOf(state);
  if (token === null || holder?.token === token) return null;
  return { key: entryKeyOf(state), step: isStepEntry(state) };
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
    // (NAV-2, см. lib/scrollMemory). С заглушки подшага «Назад» приходит на
    // заглушку того же окна под ней — тоже невидимый шаг.
    const invisible =
      overlayTokenOf(event.state) === null || (left?.step === true && ghost !== null && !ghost.step);
    if (left && invisible && entryKeyOf(event.state) === left.key) {
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

/** Запись подшага — поверх записи окна, с тем же токеном. */
function pushStepEntry(token: string): void {
  const current = window.history.state;
  const base = current && typeof current === "object" ? (current as Record<string, unknown>) : {};
  History.prototype.pushState.call(window.history, { ...base, [OVERLAY_FIELD]: token, [STEP_FIELD]: 1 }, "");
  ghost = null;
}

/** Переписать верхнюю запись под другое окно: новый токен, без подшага. */
function replaceOverlayEntry(token: string): void {
  const current = window.history.state;
  const base = current && typeof current === "object" ? { ...(current as Record<string, unknown>) } : {};
  delete base[STEP_FIELD];
  History.prototype.replaceState.call(window.history, { ...base, [OVERLAY_FIELD]: token }, "");
}

function nextToken(): string {
  sequence += 1;
  return `ov-${Date.now().toString(36)}-${sequence}`;
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
  /**
   * Открыть подшаг — экран внутри окна (поиск: «Это вы?»). onBack зовётся,
   * когда подшаг закрыло системное «Назад»: окно остаётся открытым.
   */
  pushStep: (onBack: () => void) => void;
  /** Подшаг закрыли сами (кнопка, Esc) — снять его запись из истории. */
  popStep: () => void;
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
  // Запись подшага лежит в истории над записью окна.
  const stepRef = useRef(false);
  // Мы сами сделали «назад» с подшага и ждём popstate: "silent" — просто
  // снять запись; "repush" — пока ждали, подшаг открыли снова, запись вернуть.
  const landingRef = useRef<"silent" | "repush" | null>(null);
  const stepBackRef = useRef<(() => void) | null>(null);
  // Окно уже уходит (переход по истории в пути): второй dismiss подряд
  // (двойной тап по крестику) снял бы ещё запись — уже со страницы.
  const leavingRef = useRef(false);

  const resetStep = useCallback(() => {
    stepRef.current = false;
    landingRef.current = null;
    stepBackRef.current = null;
  }, []);

  // Сколько записей окна снять, чтобы уйти на страницу. Наш «назад» с
  // подшага ещё в пути — он снимет запись подшага сам: не считаем её и не
  // возвращаем.
  const depthToLeave = useCallback((): number => {
    const leaving = landingRef.current !== null;
    if (leaving) landingRef.current = "silent";
    return stepRef.current && !leaving ? 2 : 1;
  }, []);

  // Окно закрыто, его записи в истории больше нет.
  const finish = useCallback(() => {
    window.clearTimeout(timerRef.current);
    tokenRef.current = null;
    leavingRef.current = false;
    resetStep();
    closeRef.current();
    const after = afterRef.current;
    afterRef.current = null;
    after?.();
  }, [resetStep]);

  const dismiss = useCallback(() => {
    const token = tokenRef.current;
    if (token !== null && overlayTokenOf(window.history.state) === token) {
      if (leavingRef.current) return;
      leavingRef.current = true;
      // С открытым подшагом — обе записи окна одним переходом.
      window.history.go(-depthToLeave());
      // Страховка: браузер не прислал popstate — закрываем сами.
      window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => {
        if (tokenRef.current === token) finish();
      }, BACK_FALLBACK_MS);
      return;
    }
    finish();
  }, [finish, depthToLeave]);

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
      const previous = holder;
      if (isStepEntry(window.history.state)) {
        // У прежнего окна открыт подшаг — две записи одной не заменить. Новое
        // окно встаёт на место подшага со своим токеном, а запись под ним
        // остаётся заглушкой: её перешагнёт обход заглушек выше.
        token = nextToken();
        replaceOverlayEntry(token);
      } else {
        token = holder.token;
      }
      previous.release();
    } else {
      token = nextToken();
      pushOverlayEntry(token);
    }
    tokenRef.current = token;
    const self: Holder = {
      token,
      release: () => {
        tokenRef.current = null;
        afterRef.current = null;
        leavingRef.current = false;
        resetStep();
        closeRef.current();
      },
    };
    holder = self;
    openCount += 1;
    document.documentElement.classList.add(OPEN_CLASS);

    const onPop = () => {
      const mine = tokenRef.current;
      if (mine === null) return;
      const state = window.history.state;
      if (overlayTokenOf(state) !== mine) {
        // Запись окна сняли — «Назад» или наш же history.back().
        finish();
        return;
      }
      if (isStepEntry(state)) {
        // «Вперёд» на запись подшага, который уже закрыт: показать нечего —
        // шагаем обратно, иначе следующее «Назад» ничего видимого не сделает.
        if (!stepRef.current) window.history.back();
        return;
      }
      if (!stepRef.current) return;
      // Запись подшага сняли: сами (popStep) или системным «Назад».
      stepRef.current = false;
      const landing = landingRef.current;
      landingRef.current = null;
      if (landing === "repush") {
        pushStepEntry(mine);
        stepRef.current = true;
      } else if (landing === null) {
        const onBack = stepBackRef.current;
        stepBackRef.current = null;
        onBack?.();
      }
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
      // С подшагом — обе записи. dismiss уже в пути — он их и снимет.
      if (mine !== null && !leavingRef.current && overlayTokenOf(window.history.state) === mine) {
        window.history.go(-depthToLeave());
      }
      leavingRef.current = false;
      resetStep();
    };
  }, [open, finish, dismiss, resetStep, depthToLeave]);

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

  const pushStep = useCallback((onBack: () => void) => {
    const token = tokenRef.current;
    if (token === null) return;
    stepBackRef.current = onBack;
    if (stepRef.current) {
      // Прежний подшаг только что закрыли, а его «назад» ещё в пути — запись
      // вернём, когда он дойдёт (onPop).
      if (landingRef.current === "silent") landingRef.current = "repush";
      return;
    }
    // Запись окна не на вершине — подшаг в историю не пишем: «Назад» тогда
    // закроет окно целиком, как раньше.
    if (overlayTokenOf(window.history.state) !== token) return;
    pushStepEntry(token);
    stepRef.current = true;
  }, []);

  const popStep = useCallback(() => {
    stepBackRef.current = null;
    if (!stepRef.current || tokenRef.current === null) return;
    if (landingRef.current !== null) {
      // «Назад» уже в пути — только отменяем возврат записи.
      landingRef.current = "silent";
      return;
    }
    const state = window.history.state;
    if (overlayTokenOf(state) === tokenRef.current && isStepEntry(state)) {
      landingRef.current = "silent";
      window.history.back();
      return;
    }
    // Запись подшага уже не на вершине — снимать нечего.
    stepRef.current = false;
  }, []);

  useEffect(() => () => window.clearTimeout(timerRef.current), []);

  return { dismiss, dismissThen, interceptLinks, pushStep, popStep };
}
