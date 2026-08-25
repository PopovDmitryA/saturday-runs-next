/**
 * Ссылки на протокол старта ведут внутрь сайта.
 *
 * Раньше дата пробежки/волонтёрства в кабинете уводила на 5verst.ru, s95.ru
 * или parkrun.org.uk — на сайте не было своих протоколов. Теперь они есть
 * (/locations/{слаг}/protocol/{система}/{дата}), и уводить человека наружу
 * незачем: у нас те же данные плюс места по полу и возрастной группе, история
 * площадки и отметки рекордов. Наружу остаётся одна ссылка — «Источник» на
 * самой странице протокола и страница площадки в её системе (справочно).
 *
 * Решение Дмитрия 23.08.2026.
 */

/** Локация без разобранного слага: профильный импорт не понял, где человек бежал. */
const UNKNOWN_SLUG = "unknown";

export function locationProtocolHref(
  slug: string,
  platformCode: string,
  eventDate: string,
): string {
  return `/locations/${encodeURIComponent(slug)}/protocol/${encodeURIComponent(platformCode)}/${eventDate}`;
}

export type ProtocolTarget = {
  location_slug?: string | null;
  platform_code: string;
  event_date: string;
  is_test_event?: boolean;
};

/**
 * Адрес нашего протокола — или null, если его точно не будет.
 *
 * Тестовые старты в журнал локации не попадают, и своей страницы протокола у
 * них нет: для них остаётся внешняя ссылка платформы.
 */
export function ourProtocolHref(item: ProtocolTarget): string | null {
  const slug = (item.location_slug ?? "").trim().toLowerCase();
  if (!slug || slug === UNKNOWN_SLUG || item.is_test_event) {
    return null;
  }
  return locationProtocolHref(slug, item.platform_code, item.event_date);
}
