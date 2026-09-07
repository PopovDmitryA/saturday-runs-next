/**
 * Снимок страницы на запись истории.
 *
 * Хранилище «ключ записи (см. historyEntry) → имя → значение»: сколько строк
 * таблицы догружено, раскрыт ли спойлер карты, что выбрано в фильтре столбца.
 * Всё, что должно пережить уход на другую страницу и вернуться по «назад», но
 * чему не место в адресе.
 *
 * Основное хранилище — память вкладки: снимок нужен ровно на время жизни
 * документа. Копия в sessionStorage — на случай, когда документ всё-таки
 * перезагрузится (обновление страницы, возврат с внешнего сайта мимо bfcache):
 * ключ записи лежит в history.state и переживает перезагрузку, значит по нему
 * найдётся и снимок.
 */

import { currentEntryKey } from "./historyEntry";

const STORAGE_KEY = "srs.entry-memory";
/** Записей истории помним столько: дальше «назад» уже никто не жмёт. */
const ENTRY_LIMIT = 40;

const memory = new Map<string, Record<string, unknown>>();
let hydrated = false;

function hydrate(): void {
  if (hydrated) {
    return;
  }
  hydrated = true;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return;
    }
    const parsed = JSON.parse(raw) as [string, Record<string, unknown>][];
    if (Array.isArray(parsed)) {
      for (const [key, values] of parsed) {
        if (typeof key === "string" && values && typeof values === "object") {
          memory.set(key, values);
        }
      }
    }
  } catch {
    // приватный режим или битый снимок — переживём, просто без восстановления
  }
}

function persist(): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify([...memory]));
  } catch {
    // квота или приватный режим: снимок в памяти вкладки всё равно работает
  }
}

function trim(): void {
  while (memory.size > ENTRY_LIMIT) {
    const oldest = memory.keys().next();
    if (oldest.done) {
      return;
    }
    memory.delete(oldest.value);
  }
}

export function readEntryValue<T>(name: string, key: string = currentEntryKey()): T | undefined {
  hydrate();
  const values = memory.get(key);
  if (!values || !(name in values)) {
    return undefined;
  }
  return values[name] as T;
}

/**
 * Значение обязано быть JSON-совместимым: множества и Map кладём массивами,
 * иначе копия в sessionStorage их не переживёт.
 */
export function writeEntryValue(name: string, value: unknown, key: string = currentEntryKey()): void {
  hydrate();
  const values = memory.get(key) ?? {};
  values[name] = value;
  memory.set(key, values);
  trim();
}

export function installEntryMemory(): void {
  if (typeof window === "undefined") {
    return;
  }
  hydrate();
  // pagehide ловит и закрытие вкладки, и уход на внешний сайт, и bfcache;
  // visibilitychange добавлен ради мобильной Safari, где pagehide при сворачивании
  // приложения случается не всегда.
  window.addEventListener("pagehide", persist);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      persist();
    }
  });
}
