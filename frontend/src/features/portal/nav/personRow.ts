/**
 * Подписи человека из выдачи поиска: строка поиска и карточка «Это вы?»
 * (в окне поиска и в окне после входа) показывают одни и те же цифры.
 */
import { formatDate } from "../../../lib/format";

export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

type PersonNumbers = {
  total_runs: number;
  total_volunteering: number;
  last_run_date: string | null;
};

export function personStats(person: PersonNumbers): string {
  const runs = `${person.total_runs} ${plural(person.total_runs, "пробежка", "пробежки", "пробежек")}`;
  const vol = `${person.total_volunteering} ${plural(person.total_volunteering, "волонтёрство", "волонтёрства", "волонтёрств")}`;
  // Однофамильцы упорядочены по последнему старту — без даты на экране
  // порядок выглядел случайным.
  const last = person.last_run_date ? ` · последний старт ${formatDate(person.last_run_date)}` : "";
  return `${runs} · ${vol}${last}`;
}

// «Барнаул (Барнаул)» и «Королёв (Королёв)» читаются как опечатка: город
// дописываем, только если его нет в названии локации.
export function placeWithCity(name: string, city: string | null): string {
  if (!city || name.toLowerCase().includes(city.toLowerCase())) return name;
  return `${name} (${city})`;
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
}
