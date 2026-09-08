/**
 * «Закрыть до следующей субботы» — общая память для плашек кабинета, которые
 * человек не хочет видеть повторно: момент «поделиться» и блок оценок.
 *
 * Суббота — потому что это день старта: всё, что плашки предлагают, обновляется
 * новым стартом. Закрытое в воскресенье не мешает всю неделю, а в субботу,
 * когда есть новый повод, показывается снова. Хранится только в браузере.
 */

const PREFIX = "srs.dismissed.";

function nextSaturday(from: Date): Date {
  const next = new Date(from.getFullYear(), from.getMonth(), from.getDate());
  // 6 — суббота. Если сегодня суббота, прячем до следующей: этот старт уже был.
  const days = ((6 - next.getDay() + 7) % 7) || 7;
  next.setDate(next.getDate() + days);
  return next;
}

export function isDismissedThisWeek(key: string): boolean {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    if (!raw) {
      return false;
    }
    const until = Number(raw);
    if (!Number.isFinite(until) || Date.now() >= until) {
      localStorage.removeItem(PREFIX + key);
      return false;
    }
    return true;
  } catch {
    return false;
  }
}

export function dismissUntilNextSaturday(key: string): void {
  try {
    localStorage.setItem(PREFIX + key, String(nextSaturday(new Date()).getTime()));
  } catch {
    // localStorage недоступен — плашка просто покажется снова
  }
}
