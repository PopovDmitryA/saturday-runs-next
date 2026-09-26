/**
 * «Найти себя в статистике» — поиск с подсказкой «введите фамилию и имя».
 *
 * Гостей на сайте около 82%, и главная звала их сразу на вход. Но ценность
 * входа видна только тогда, когда человек увидел себя в протоколах: «вот вы,
 * 30 пробежек». Поэтому кнопка открывает поиск, а вход — на расстоянии одного
 * касания прямо в выдаче («Нашли себя? Войдите…»).
 *
 * Окно поиска одно на всё приложение, общаемся тем же событием, что и
 * openSiteSearch, с пометкой режима.
 */
import { SITE_SEARCH_OPEN_EVENT, type SiteSearchOpenDetail } from "./siteSearchBus";

export type { SiteSearchOpenMode } from "./siteSearchBus";

/** Прежнее имя типа: режим теперь есть в самом SiteSearchOpenDetail. */
export type SiteSearchOpenDetailWithMode = SiteSearchOpenDetail;

export function openFindSelfSearch(): void {
  window.dispatchEvent(
    new CustomEvent<SiteSearchOpenDetailWithMode>(SITE_SEARCH_OPEN_EVENT, { detail: { mode: "find-self" } }),
  );
}
