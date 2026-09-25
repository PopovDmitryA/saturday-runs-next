/**
 * Открыть поиск по сайту из любого места: кнопки в шапке, «Меню», страница
 * 404. Само окно поиска одно на всё приложение (SiteSearchDialog в App),
 * поэтому общаемся событием, а не общим контекстом через половину дерева.
 */
export const SITE_SEARCH_OPEN_EVENT = "site-search:open";

export type SiteSearchOpenDetail = {
  /** Подставить в поле готовый запрос (например, слова из адреса на 404). */
  query?: string;
};

export function openSiteSearch(query?: string): void {
  window.dispatchEvent(
    new CustomEvent<SiteSearchOpenDetail>(SITE_SEARCH_OPEN_EVENT, { detail: query ? { query } : {} }),
  );
}
