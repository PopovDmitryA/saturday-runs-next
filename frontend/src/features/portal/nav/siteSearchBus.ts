/**
 * Открыть поиск по сайту из любого места: кнопки в шапке, рельсе, шторке
 * «Меню». Само окно поиска одно на всё приложение (SiteSearchDialog в App),
 * поэтому общаемся событием, а не общим контекстом через половину дерева.
 */
export const SITE_SEARCH_OPEN_EVENT = "site-search:open";

export function openSiteSearch(): void {
  window.dispatchEvent(new CustomEvent(SITE_SEARCH_OPEN_EVENT));
}
