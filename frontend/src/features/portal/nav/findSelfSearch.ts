/**
 * «Найти себя в статистике» — поиск с подсказкой «введите фамилию и имя».
 *
 * Гостей на сайте около 82%, и главная звала их сразу на вход. Но ценность
 * входа видна только тогда, когда человек увидел себя в протоколах: «вот вы,
 * 30 пробежек». Поэтому кнопка открывает поиск, а вход — прямо из выдачи:
 * нажал на свою строку → «Это вы?» → «Войти и привязать», и после входа сайт
 * предложит привязать именно этого человека (nav/searchClaim.ts).
 *
 * Окно поиска одно на всё приложение, общаемся тем же событием, что и
 * openSiteSearch, с пометкой режима.
 */
import { SITE_SEARCH_OPEN_EVENT, type SiteSearchOpenDetail } from "./siteSearchBus";

export type { SiteSearchOpenMode } from "./siteSearchBus";

/** Прежнее имя типа: режим теперь есть в самом SiteSearchOpenDetail. */
export type SiteSearchOpenDetailWithMode = SiteSearchOpenDetail;

/** query — сразу искать это имя (ссылка «Это вы?» устарела — найти себя заново). */
export function openFindSelfSearch(query?: string): void {
  window.dispatchEvent(
    new CustomEvent<SiteSearchOpenDetailWithMode>(SITE_SEARCH_OPEN_EVENT, {
      detail: query ? { mode: "find-self", query } : { mode: "find-self" },
    }),
  );
}
