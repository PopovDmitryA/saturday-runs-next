/**
 * Подсказка сайдбару: какая локация открыта.
 *
 * Сайт — MPA: переход «Локация → Журнал протоколов» перезагружает страницу, и
 * пока не приехал ответ API, названия локации ещё нет — подпункт сайдбара на
 * долю секунды исчезал и появлялся снова (репорт Дмитрия 11.08.2026). Имя
 * последней открытой площадки держим в sessionStorage и читаем синхронно на
 * первом рендере — сайдбар рисуется сразу правильным.
 */
const STORAGE_KEY = "lastLocationHint";

export type LocationHint = { slug: string; name: string };

export function rememberLocationHint(hint: LocationHint): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(hint));
  } catch {
    // приватный режим / переполнение — просто не запоминаем
  }
}

/** Подсказка для этого слага; для чужого — undefined, чужое имя хуже пустого. */
export function locationHintFor(slug: string): LocationHint | undefined {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return undefined;
    }
    const parsed = JSON.parse(raw) as Partial<LocationHint>;
    if (parsed.slug === slug && typeof parsed.name === "string" && parsed.name) {
      return { slug, name: parsed.name };
    }
  } catch {
    // битое значение — ведём себя как будто подсказки нет
  }
  return undefined;
}
