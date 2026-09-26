/**
 * Ключ у записи истории браузера.
 *
 * Роутинг у нас свой (pushState + перерисовка, см. hooks/useAppPath), и
 * «назад» обязан возвращать страницу такой, какой её оставили: та же позиция
 * прокрутки, те же раскрытые блоки, столько же догруженных строк таблицы.
 * Привязать этот снимок к адресу нельзя — на одном адресе записей истории
 * бывает несколько (открыл рейтинг, ушёл в профиль, вернулся, ушёл снова), —
 * поэтому каждая запись получает собственный ключ, а по ключу лежит её снимок
 * (см. entryMemory) и позиция прокрутки (см. scrollMemory).
 *
 * Ключ живёт прямо в history.state. Страницы по всему сайту зовут
 * pushState/replaceState напрямую и передают state=null, поэтому дешевле один
 * раз обернуть сами методы, чем править два десятка мест и следить, чтобы
 * следующее новое место не забыло про ключ.
 */

const KEY_FIELD = "srsEntry";
/**
 * Метка записи-заглушки окна поверх страницы (см. nav/useOverlayHistory).
 * Здесь о ней знаем только затем, чтобы replaceState страницы её не стёр.
 */
const OVERLAY_FIELD = "srsOverlay";

export type EntryChange = {
  /** Запись, которую покидаем: под этим ключом сохраняется её снимок. */
  from: string;
  /** Запись, на которую перешли. */
  to: string;
  /** «pop» — движение по истории (назад/вперёд), «push» — новый переход. */
  reason: "push" | "pop";
};

const listeners = new Set<(change: EntryChange) => void>();
let activeKey = "";
let installed = false;

function makeKey(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function stringField(state: unknown, field: string): string | null {
  if (state && typeof state === "object") {
    const value = (state as Record<string, unknown>)[field];
    if (typeof value === "string" && value) {
      return value;
    }
  }
  return null;
}

function keyOf(state: unknown): string | null {
  return stringField(state, KEY_FIELD);
}

function withKey(state: unknown, key: string): Record<string, unknown> {
  const base = state && typeof state === "object" ? { ...(state as Record<string, unknown>) } : {};
  base[KEY_FIELD] = key;
  return base;
}

function emit(change: EntryChange): void {
  for (const listener of listeners) {
    listener(change);
  }
}

export function currentEntryKey(): string {
  if (!activeKey) {
    // Обёртка ещё не установлена (тесты, ранний импорт) — ключ всё равно нужен,
    // иначе снимки писать некуда.
    activeKey = keyOf(window.history.state) ?? makeKey();
  }
  return activeKey;
}

export function onEntryChange(listener: (change: EntryChange) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function installHistoryEntries(): void {
  if (installed || typeof window === "undefined") {
    return;
  }
  installed = true;
  const pushState = window.history.pushState.bind(window.history);
  const replaceState = window.history.replaceState.bind(window.history);

  // Запись открытой страницы могла достаться от прошлой жизни вкладки
  // (перезагрузка, возврат с внешнего сайта) — тогда ключ уже в history.state,
  // и снимок из sessionStorage найдётся по нему.
  activeKey = keyOf(window.history.state) ?? makeKey();
  replaceState(withKey(window.history.state, activeKey), "");

  window.history.pushState = (state: unknown, unused: string, url?: string | URL | null) => {
    const from = activeKey;
    const next = makeKey();
    pushState(withKey(state, next), unused, url);
    activeKey = next;
    emit({ from, to: next, reason: "push" });
  };

  window.history.replaceState = (state: unknown, unused: string, url?: string | URL | null) => {
    // Это та же запись истории — ключ и её снимок остаются прежними. Так
    // страницы могут править адрес под фильтры, не теряя позицию прокрутки.
    const next = withKey(state, activeKey);
    // Страница поправила адрес (канонический slug локации, номер страницы
    // «Обновлений», адрес профиля — после ответа API), пока открыто окно и на
    // вершине истории его заглушка. Страницы передают state=null и метку окна
    // стирали: окно, закрытое кнопкой или Esc, больше не узнавало свою запись,
    // не снимало её, и первое «Назад» после этого ничего видимого не делало
    // (ревью перед пушем 26.09.2026, NAV-3). Метку переносим, если страница
    // не передала свою.
    const overlay = stringField(window.history.state, OVERLAY_FIELD);
    if (overlay !== null && !(OVERLAY_FIELD in next)) {
      next[OVERLAY_FIELD] = overlay;
    }
    replaceState(next, unused, url);
  };

  // Слушатель ставится до монтирования React (см. main.tsx), поэтому к моменту
  // работы остальных обработчиков popstate ключ уже переключён.
  window.addEventListener("popstate", (event) => {
    const from = activeKey;
    const stored = keyOf(event.state);
    const to = stored ?? makeKey();
    if (!stored) {
      // Запись без ключа: её оставили до установки обёртки. Доклеиваем, чтобы
      // дальше она вела себя как все.
      replaceState(withKey(event.state, to), "");
    }
    activeKey = to;
    emit({ from, to, reason: "pop" });
  });
}
