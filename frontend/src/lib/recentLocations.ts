/**
 * Недавно открытые локации — для колонки «Локации» и поиска («погода» без
 * названия парка ведёт в погоду той локации, где человек был недавно).
 *
 * Хранится в localStorage этого браузера: список короткий, личный и нужен
 * только для подсказок, серверу его знать незачем. Пишется из
 * rememberLocationHint — то есть с любой страницы локации.
 */
export type RecentLocation = { slug: string; name: string };

const STORAGE_KEY = "recentLocations:v1";
const LIMIT = 5;

export function getRecentLocations(): RecentLocation[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (item): item is RecentLocation =>
          typeof item === "object" &&
          item !== null &&
          typeof (item as RecentLocation).slug === "string" &&
          typeof (item as RecentLocation).name === "string" &&
          (item as RecentLocation).name.length > 0,
      )
      .slice(0, LIMIT);
  } catch {
    return [];
  }
}

export function rememberRecentLocation(location: RecentLocation): void {
  try {
    const rest = getRecentLocations().filter((item) => item.slug !== location.slug);
    localStorage.setItem(STORAGE_KEY, JSON.stringify([location, ...rest].slice(0, LIMIT)));
  } catch {
    // приватный режим — просто без истории
  }
}
