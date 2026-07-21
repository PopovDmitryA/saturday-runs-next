// Локальный «не предлагать оценить этот старт»: общий для всех мест, где мы
// подсказываем оценку (блок на дашборде, карточка на странице локации).
// Хранится только в браузере — на сам факт возможности оценить не влияет,
// старт всегда остаётся доступен под ⭐ в «Пробежках»/«Волонтёрстве».
const DISMISSED_STORAGE_KEY = "recentRatingsDismissed";

export function loadDismissedRatings(): Set<string> {
  try {
    const raw = localStorage.getItem(DISMISSED_STORAGE_KEY);
    return raw ? new Set(JSON.parse(raw) as string[]) : new Set();
  } catch {
    return new Set();
  }
}

export function saveDismissedRatings(ids: Set<string>) {
  try {
    localStorage.setItem(DISMISSED_STORAGE_KEY, JSON.stringify([...ids]));
  } catch {
    // localStorage недоступен — просто не сохраняем
  }
}
