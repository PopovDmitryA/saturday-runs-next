import { readCached } from "../../lib/dataCache";
import { formatInt } from "../../lib/format";
import { verbForCount } from "../planner/PlanPeople";
import { SERIES_ICON } from "../planner/seriesText";
import type { GuestHome, OrganizerGuests } from "../planner/plannerApi";

// Общее у «Кто у нас бежит» и страницы участника: адреса, кэш списка и подпись
// домашней локации.

export const GUESTS_TOOL_LABEL = "Кто у нас бежит";

// Горизонт списка — три месяца: дальше планов почти не бывает, а вкладок и так до двенадцати.
export const GUESTS_WEEKS = 12;

/** Ключ ответа «Кто у нас бежит» в кэше вкладки (lib/dataCache). */
export function organizerGuestsCacheKey(slug: string): string {
  return `planner:organizer-guests:${slug}:${GUESTS_WEEKS}`;
}

/**
 * Имя человека и название локации из списка «Кто у нас бежит», если список
 * открывали в этой вкладке недавно (кэш «назад» — пять минут). Страница
 * участника пишет их, пока едет её собственный ответ: в заголовке сразу имя,
 * а не «Участник». Списка нет (прямой заход, переход из календаря) — null.
 */
export function listedGuestHint(slug: string, handle: string): { name: string | null; locationName: string } | null {
  const list = readCached<OrganizerGuests>(organizerGuestsCacheKey(slug));
  if (!list) {
    return null;
  }
  const row = [...list.dates.flatMap((item) => item.people), ...(list.regulars_people ?? [])].find(
    (item) => item.handle === handle,
  );
  return { name: row?.name?.trim() || null, locationName: list.location.name };
}

export function organizerGuestsHref(slug: string): string {
  return `/organizer/${slug}/guests`;
}

export function organizerGuestHref(slug: string, handle: string): string {
  return `${organizerGuestsHref(slug)}/${encodeURIComponent(handle)}`;
}

/** Город, если он не повторяет название локации: «Зеленоград, Зеленоград» ни к чему. */
export function homeCity(home: GuestHome): string | null {
  const city = home.city?.trim();
  if (!city || home.name.toLowerCase().includes(city.toLowerCase())) {
    return null;
  }
  return city;
}

/**
 * «Кузьминки, Москва» — домашняя локация ссылкой и город. Домашняя — эта же
 * локация: «ваша локация», без ссылки на страницу, на которой организатор и так.
 * «Эта же» решает сервер по ключу идентичности (home.here): слаг дома и слаг
 * страницы у одной локации бывают разными (Раменское).
 */
export function GuestHomePlace({ home }: { home: GuestHome }) {
  if (home.here) {
    return <>ваша локация</>;
  }
  const city = homeCity(home);
  return (
    <>
      <a href={`/locations/${encodeURIComponent(home.slug)}`}>{home.name}</a>
      {city && `, ${city}`}
    </>
  );
}

/**
 * «Планируют 41 · из них ↻ постоянных 9» — «Кто у нас бежит» и будущий день
 * оргкалендаря, где разовые и постоянные одним списком (решение Дмитрия
 * 27.10.2026, как на странице локации): total — все, как число во вкладке.
 */
export function plannersLine(total: number, regulars: number): string {
  const head = `${verbForCount(total, "Планирует", "Планируют")} ${formatInt(total)}`;
  return regulars > 0 ? `${head} · из них ${SERIES_ICON} постоянных ${formatInt(regulars)}` : head;
}
