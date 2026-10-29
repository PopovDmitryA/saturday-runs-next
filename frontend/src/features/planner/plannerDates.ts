// Даты планировщика. Всё в местном времени браузера и без toISOString():
// в Москве он делает из субботы пятницу (см. lib/protocolHref.ts).

const MONTHS_NOMINATIVE = [
  "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
  "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
];

const MONTHS_GENITIVE = [
  "января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря",
];

/** Понедельник первый — как в российском календаре. */
export const WEEKDAYS_SHORT_MON_FIRST = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];

const WEEKDAYS_SHORT_SUN_FIRST = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];

/**
 * Сегодня по Москве. По нему сервер считает «прошло / впереди» и окно дат
 * планов, а браузер в другом поясе после полуночи жил бы уже завтрашним днём.
 */
export function moscowTodayIso(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow" }).format(new Date());
}

/** "YYYY-MM-DD" → Date в местном времени (полночь). */
export function parseIsoDay(value: string): Date {
  const [y, m, d] = value.split("-").map(Number);
  return new Date(y, (m || 1) - 1, d || 1);
}

/** Date → "YYYY-MM-DD" по местному календарю. */
export function toIsoDay(value: Date): string {
  const y = value.getFullYear();
  const m = String(value.getMonth() + 1).padStart(2, "0");
  const d = String(value.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export function addDays(value: Date, days: number): Date {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate() + days);
}

/** 0 — понедельник … 6 — воскресенье. */
export function weekdayMonFirst(value: Date): number {
  return (value.getDay() + 6) % 7;
}

/** «Октябрь 2026». */
export function formatMonthTitle(year: number, monthIndex: number): string {
  return `${MONTHS_NOMINATIVE[monthIndex]} ${year}`;
}

/** «31 октября»; с year — «31 октября 2026». */
export function formatDayMonth(iso: string, withYear = false): string {
  const day = parseIsoDay(iso);
  const base = `${day.getDate()} ${MONTHS_GENITIVE[day.getMonth()]}`;
  return withYear ? `${base} ${day.getFullYear()}` : base;
}

// Короткие месяцы для вкладок дат: «мая» короче не бывает, остальные — три буквы.
const MONTHS_GENITIVE_SHORT = [
  "янв", "фев", "мар", "апр", "мая", "июн",
  "июл", "авг", "сен", "окт", "ноя", "дек",
];

/** «31 окт» — вкладка даты в блоке «Планируют пробежать». */
export function formatDayMonthShort(iso: string): string {
  const day = parseIsoDay(iso);
  return `${day.getDate()} ${MONTHS_GENITIVE_SHORT[day.getMonth()]}`;
}

/** «сб 31 октября». */
export function formatWeekdayDayMonth(iso: string, withYear = false): string {
  const day = parseIsoDay(iso);
  return `${WEEKDAYS_SHORT_SUN_FIRST[day.getDay()]} ${formatDayMonth(iso, withYear)}`;
}

/** «сб 31 окт» — короткая подпись для тесных мест (галочки суббот постоянного плана). */
export function formatWeekdayDayMonthShort(iso: string): string {
  const day = parseIsoDay(iso);
  return `${WEEKDAYS_SHORT_SUN_FIRST[day.getDay()]} ${formatDayMonthShort(iso)}`;
}

/** «сб 31 октября»; год — только если он не текущий. capital — «Сб 31 октября». */
export function planDayLabel(iso: string, capital = false): string {
  const label = formatWeekdayDayMonth(iso, parseIsoDay(iso).getFullYear() !== new Date().getFullYear());
  return capital ? label.charAt(0).toUpperCase() + label.slice(1) : label;
}

/** «YYYY-MM» месяца даты. */
export function monthKey(value: Date): string {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}`;
}

/** Сдвиг «YYYY-MM» на delta месяцев. */
export function shiftMonthKey(key: string, delta: number): string {
  const [y, m] = key.split("-").map(Number);
  return monthKey(new Date(y, m - 1 + delta, 1));
}

/**
 * Клетки месяца для сетки пн–вс: даты от понедельника первой недели до
 * воскресенья последней. inMonth=false — хвосты соседних месяцев.
 */
export function monthGrid(key: string): { iso: string; day: number; inMonth: boolean }[] {
  const [y, m] = key.split("-").map(Number);
  const first = new Date(y, m - 1, 1);
  const last = new Date(y, m, 0);
  const start = addDays(first, -weekdayMonFirst(first));
  const end = addDays(last, 6 - weekdayMonFirst(last));
  const cells: { iso: string; day: number; inMonth: boolean }[] = [];
  for (let cursor = start; cursor <= end; cursor = addDays(cursor, 1)) {
    cells.push({ iso: toIsoDay(cursor), day: cursor.getDate(), inMonth: cursor.getMonth() === m - 1 });
  }
  return cells;
}

/** Целых дней от сегодня до даты (0 — сегодня, 1 — завтра). */
export function daysUntil(iso: string, today: Date = new Date()): number {
  const target = parseIsoDay(iso);
  const base = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  return Math.round((target.getTime() - base.getTime()) / 86_400_000);
}

type StartFacts = {
  number_forecast: number | null;
};

/**
 * «≈№214» — подробности старта для строки. Времени старта в планировщике нет
 * (решение Дмитрия 26.10.2026): план — про дату, время перегружало окно и
 * календарь. Напоминание накануне время по-прежнему пишет.
 */
export function startFacts(start: StartFacts): string[] {
  return start.number_forecast != null ? [`≈№${start.number_forecast}`] : [];
}
