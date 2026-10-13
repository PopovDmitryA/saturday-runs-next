/**
 * Возврат на страницу, с которой нажали «Войти».
 *
 * Носитель — параметр `?next=` у /login: `/login?next=%2Flocations%2Fbitsa`.
 * Страница входа отдаёт его каждому способу входа, сервер хранит его рядом
 * со своим состоянием (state OAuth, заявка бота, state виджета Telegram) и
 * сам решает, куда вести после входа: новичка — сначала в онбординг
 * (/welcome?next=…), остальных — прямо на исходную страницу. Сервер же
 * проверяет адрес по своим правилам (backend/app/core/return_path.py) —
 * здесь их копия, чтобы не строить заведомо отвергнутых ссылок.
 *
 * Ссылки «Войти» в разметке остаются голым `/login`: в пререндере и у
 * роботов не плодятся адреса с `?next=`, а сам адрес возврата снимается в
 * момент нажатия (installLoginLinkReturn) — с текущими фильтрами и якорем,
 * которые страница могла поменять через replaceState уже после рендера.
 * Программные переходы на вход (RequireAuth, голос в бэклоге без входа)
 * идут через goToLogin().
 *
 * Провайдеры (VK, Яндекс, Telegram) возвращают человека на свои служебные
 * страницы, и наши параметры до них не доезжают. На случай ошибки там
 * адрес возврата лежит в sessionStorage этой вкладки (rememberLoginNext),
 * чтобы «вернуться ко входу» не потеряло исходную страницу.
 */

export const LOGIN_PATH = "/login";
export const LOGIN_NEXT_PARAM = "next";

// Ссылка с этим атрибутом ведёт на вход без возврата (выход из профиля:
// после него возвращаться на личную страницу бессмысленно).
export const LOGIN_PLAIN_ATTR = "data-login-plain";
// Пометка ссылок, которым адрес возврата дописали мы, а не разметка: такой
// адрес пересчитывается на каждом нажатии, явный — не трогается.
const AUTO_NEXT_ATTR = "data-login-next-auto";

const MAX_RETURN_PATH_LENGTH = 1024;
const EXCLUDED_FIRST_SEGMENTS = new Set(["api", "auth", "login", "oauth", "render", "welcome"]);

// Сегмент «.» или «..», в том числе закодированный (%2e): браузер схлопывает
// их при разборе адреса, и «/x/..//evil.com» становится «//evil.com» — адресом
// чужого сайта (находка ревью 12.10.2026). Живая страница сайта таких
// сегментов не содержит: браузер уже убрал их из location.
const DOT_SEGMENT_RE = /(^|\/)(\.|%2e){1,2}(?=\/|$)/i;

const NEXT_STORAGE_KEY = "sr_login_next";
// Вход по почте занимает до 10 минут (срок кода), OAuth — пару минут.
// Дольше — это уже другой заход.
const NEXT_STORAGE_TTL_MS = 30 * 60 * 1000;

function hasControlChars(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code < 0x20 || code === 0x7f) {
      return true;
    }
  }
  return false;
}

function firstSegment(pathname: string): string {
  let decoded = pathname;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    // битая кодировка — сравниваем как есть
  }
  return decoded.replace(/^\/+/, "").split("/", 1)[0].toLowerCase();
}

/** Путь внутри сайта, куда можно вернуть после входа, или null. */
export function safeReturnPath(raw: unknown): string | null {
  if (typeof raw !== "string") {
    return null;
  }
  const candidate = raw.trim();
  if (!candidate || candidate.length > MAX_RETURN_PATH_LENGTH) {
    return null;
  }
  if (!candidate.startsWith("/") || candidate.startsWith("//") || candidate.includes("\\")) {
    return null;
  }
  if (hasControlChars(candidate)) {
    return null;
  }
  if (DOT_SEGMENT_RE.test(candidate.split(/[?#]/, 1)[0])) {
    return null;
  }
  let url: URL;
  try {
    url = new URL(candidate, window.location.origin);
  } catch {
    return null;
  }
  if (url.origin !== window.location.origin) {
    return null;
  }
  if (EXCLUDED_FIRST_SEGMENTS.has(firstSegment(url.pathname))) {
    return null;
  }
  const normalized = `${url.pathname}${url.search}${url.hash}`;
  // Страховка на случай иного способа нормализации: результат обязан
  // остаться путём этого сайта, а не протокол-относительным адресом. Как и
  // сервер, не пропускаем и закодированное «/%2F%2F…».
  let decodedPath = url.pathname;
  try {
    decodedPath = decodeURIComponent(url.pathname);
  } catch {
    // битая кодировка — проверяем как есть
  }
  if (normalized.startsWith("//") || normalized.includes("\\") || decodedPath.startsWith("//")) {
    return null;
  }
  return normalized.length <= MAX_RETURN_PATH_LENGTH ? normalized : null;
}

/** Текущая страница как адрес возврата; на самом входе и в онбординге — null. */
export function currentReturnPath(): string | null {
  const { pathname, search, hash } = window.location;
  return safeReturnPath(`${pathname}${search}${hash}`);
}

/** Адрес страницы входа с возвратом на `next` (по умолчанию — текущую страницу). */
export function loginHref(next: string | null = currentReturnPath()): string {
  const safe = safeReturnPath(next);
  return safe ? `${LOGIN_PATH}?${LOGIN_NEXT_PARAM}=${encodeURIComponent(safe)}` : LOGIN_PATH;
}

function isLoginPathname(pathname: string): boolean {
  return pathname.replace(/\/+$/, "") === LOGIN_PATH;
}

/**
 * Дописывает адрес возврата к ссылке на вход, сохраняя её прочие параметры
 * (`/login?oauth_error=…`). Ссылка не на вход или с уже заданным next
 * возвращается как есть.
 */
export function withLoginReturn(href: string, next: string | null = currentReturnPath()): string {
  let url: URL;
  try {
    url = new URL(href, window.location.href);
  } catch {
    return href;
  }
  if (url.origin !== window.location.origin || !isLoginPathname(url.pathname)) {
    return href;
  }
  if (url.searchParams.has(LOGIN_NEXT_PARAM)) {
    return href;
  }
  const safe = safeReturnPath(next);
  if (!safe) {
    return href;
  }
  url.searchParams.set(LOGIN_NEXT_PARAM, safe);
  return `${url.pathname}${url.search}${url.hash}`;
}

/** Уйти на вход с возвратом на текущую страницу (или на `next`). */
export function goToLogin(options: { next?: string | null; replace?: boolean } = {}): void {
  const next = options.next === undefined ? currentReturnPath() : options.next;
  const target = loginHref(next);
  if (options.replace) {
    window.location.replace(target);
  } else {
    window.location.href = target;
  }
}

/** Адрес возврата из строки запроса страницы входа (уже проверенный). */
export function readLoginNext(search: string = window.location.search): string | null {
  return safeReturnPath(new URLSearchParams(search).get(LOGIN_NEXT_PARAM));
}

type StoredNext = { next: string; savedAt: number };

/** Запомнить адрес возврата на время ухода к провайдеру (только эта вкладка). */
export function rememberLoginNext(next: string | null): void {
  const safe = safeReturnPath(next);
  try {
    if (!safe) {
      window.sessionStorage.removeItem(NEXT_STORAGE_KEY);
      return;
    }
    const payload: StoredNext = { next: safe, savedAt: Date.now() };
    window.sessionStorage.setItem(NEXT_STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // приватный режим: без запаса, вход всё равно состоится
  }
}

/** Адрес возврата, запомненный страницей входа этой вкладки, если он свежий. */
export function recallLoginNext(): string | null {
  try {
    const raw = window.sessionStorage.getItem(NEXT_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<StoredNext> | null;
    if (!parsed || typeof parsed.savedAt !== "number" || Date.now() - parsed.savedAt > NEXT_STORAGE_TTL_MS) {
      window.sessionStorage.removeItem(NEXT_STORAGE_KEY);
      return null;
    }
    return safeReturnPath(parsed.next);
  } catch {
    return null;
  }
}

export function forgetLoginNext(): void {
  try {
    window.sessionStorage.removeItem(NEXT_STORAGE_KEY);
  } catch {
    // хранилище недоступно — и забывать нечего
  }
}

/**
 * Адрес «вернуться ко входу» со служебных страниц возврата от провайдера:
 * `/login?<query>&next=<запомненная страница>`.
 */
export function loginRetryHref(query: Record<string, string> = {}): string {
  const params = new URLSearchParams(query);
  const next = recallLoginNext();
  if (next) {
    params.set(LOGIN_NEXT_PARAM, next);
  }
  const text = params.toString();
  return text ? `${LOGIN_PATH}?${text}` : LOGIN_PATH;
}

function rewriteLoginAnchor(event: Event): void {
  const target = event.target as Element | null;
  const anchor = target?.closest?.("a[href]") as HTMLAnchorElement | null;
  if (!anchor || anchor.hasAttribute(LOGIN_PLAIN_ATTR)) {
    return;
  }
  const href = anchor.getAttribute("href");
  if (!href) {
    return;
  }
  let url: URL;
  try {
    url = new URL(href, window.location.href);
  } catch {
    return;
  }
  if (url.origin !== window.location.origin || !isLoginPathname(url.pathname)) {
    return;
  }
  const auto = anchor.hasAttribute(AUTO_NEXT_ATTR);
  if (url.searchParams.has(LOGIN_NEXT_PARAM) && !auto) {
    return;
  }
  const next = currentReturnPath();
  if (next) {
    url.searchParams.set(LOGIN_NEXT_PARAM, next);
  } else {
    url.searchParams.delete(LOGIN_NEXT_PARAM);
  }
  anchor.setAttribute("href", `${url.pathname}${url.search}${url.hash}`);
  anchor.setAttribute(AUTO_NEXT_ATTR, "");
}

/**
 * Один перехватчик на весь документ: перед переходом по любой ссылке на
 * /login дописывает ей `?next=` с текущей страницей. Висит на window в фазе
 * перехвата — это самая ранняя точка, раньше роутера (useAppPath) и
 * перехвата ссылок в окнах поверх страницы (useOverlayHistory): оба читают
 * href в момент клика. pointerdown и contextmenu — для «открыть в новой
 * вкладке» средней кнопкой и из контекстного меню (браузер берёт href до
 * click).
 */
export function installLoginLinkReturn(): () => void {
  const events = ["pointerdown", "click", "auxclick", "contextmenu"] as const;
  for (const name of events) {
    window.addEventListener(name, rewriteLoginAnchor, true);
  }
  return () => {
    for (const name of events) {
      window.removeEventListener(name, rewriteLoginAnchor, true);
    }
  };
}
