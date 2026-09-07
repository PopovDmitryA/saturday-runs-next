/**
 * Кэш ответов API на время жизни вкладки (stale-while-revalidate).
 *
 * Возврат «назад» размонтирует страницу и монтирует её заново — со всеми
 * запросами. Пока они идут, экран пуст: восстанавливать позицию прокрутки
 * некуда (см. scrollMemory), да и мигание скелетов на каждом «назад» само по
 * себе неприятно. Поэтому страница сначала рисуется из кэша, а свежие данные
 * подъезжают следом и тихо заменяют показанные.
 *
 * Кэш живёт только в памяти вкладки: ответы бывают на мегабайт (тысяча строк
 * рейтинга), в sessionStorage им не место. Подключается пострадочно — страница
 * сама решает, можно ли ей показать данные пятиминутной давности.
 */

type CacheEntry = { value: unknown; at: number };

const DEFAULT_TTL_MS = 5 * 60 * 1000;
/**
 * Больше держать незачем: это кэш «назад», а не хранилище. Записей мало
 * намеренно — один ответ рейтинга на тысячу строк весит ~0,6 МБ JSON (в памяти
 * в разы больше), и десятки таких копий на телефоне были бы дороже той секунды,
 * которую они экономят.
 */
const ENTRY_LIMIT = 8;

const store = new Map<string, CacheEntry>();

export function readCached<T>(key: string, ttlMs: number = DEFAULT_TTL_MS): T | undefined {
  const entry = store.get(key);
  if (!entry) {
    return undefined;
  }
  if (Date.now() - entry.at > ttlMs) {
    store.delete(key);
    return undefined;
  }
  return entry.value as T;
}

export function writeCached(key: string, value: unknown): void {
  // Просроченное выкидываем сразу, а не ждём, пока его вытеснят по счётчику:
  // держать в памяти мегабайты, которые уже никому не отдадим, незачем.
  const now = Date.now();
  for (const [oldKey, entry] of store) {
    if (now - entry.at > DEFAULT_TTL_MS) {
      store.delete(oldKey);
    }
  }
  store.set(key, { value, at: now });
  while (store.size > ENTRY_LIMIT) {
    const oldest = store.keys().next();
    if (oldest.done) {
      return;
    }
    store.delete(oldest.value);
  }
}

/**
 * Сбросить всё, что начинается с префикса. Зовётся после действий, которые
 * данные меняют (правка профиля, привязка, админские операции), — показывать
 * после них свой же вчерашний ответ нельзя.
 */
export function dropCached(prefix: string): void {
  for (const key of [...store.keys()]) {
    if (key.startsWith(prefix)) {
      store.delete(key);
    }
  }
}
