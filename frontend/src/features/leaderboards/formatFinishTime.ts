/**
 * Компактное время финиша: «17:23», а не «00:17:23» — колонка узкая, часы на
 * пятёрке редкость. Живёт отдельным модулем, потому что тем же видом время
 * показывают и рейтинги-лидерборды («Лучшее время»), и рейтинг быстрых, где
 * время — главная колонка таблицы.
 */
export function formatFinishTime(seconds: number | null | undefined, fallback?: string | null): string | null {
  if (seconds == null || seconds <= 0) {
    return fallback ?? null;
  }
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
  }
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}
