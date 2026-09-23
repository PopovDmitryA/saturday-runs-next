/**
 * Поиск по страницам сайта — на клиенте, по дереву siteNav и его синонимам.
 *
 * Сервер про страницы ничего не знает: дерево навигации живёт во фронте, и
 * держать второй список на бэкенде значило бы снова завести два меню, которые
 * разъедутся. Сервер ищет локации и людей, а страницы находятся здесь.
 *
 * Слова запроса делятся на две кучки: те, что узнались как страница локации
 * («погода», «протоколы», «топ»), и остальные. Остальные уходят на сервер как
 * запрос локаций и людей, а найденные локации склеиваются со страницей:
 * «погода сокол» → «Погода · Сокольники».
 */
import type { ReactNode } from "react";
import type { SiteSearchLocation } from "../../../lib/api";
import { LOCATION_PAGES, flattenNav, type NavSection } from "./siteNav";

export type PageHit = {
  key: string;
  label: string;
  /** Где страница живёт: «Рейтинги · Туристы», «Мой кабинет». */
  context: string;
  href: string;
  icon?: ReactNode;
  score: number;
};

export function normalizeQuery(raw: string): string {
  return raw
    .toLowerCase()
    .replace(/ё/g, "е")
    .replace(/[^\p{L}\p{N}\s-]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function tokens(text: string): string[] {
  return normalizeQuery(text).split(/[\s-]+/).filter(Boolean);
}

/**
 * Грубая основа слова: у русского слова отрезаем окончание, чтобы «погоду»,
 * «протоколах» и «туристов» находили «погода», «протоколы», «туристы».
 * Настоящий стеммер здесь избыточен — словарь маленький, а ложные совпадения
 * на коротких основах отсекает минимальная длина.
 */
function stem(word: string): string {
  if (word.length >= 7) return word.slice(0, -2);
  if (word.length >= 5) return word.slice(0, -1);
  return word;
}

function wordMatches(word: string, haystack: string[]): { hit: boolean; exact: boolean } {
  const base = stem(word);
  let hit = false;
  for (const token of haystack) {
    if (token === word) return { hit: true, exact: true };
    if (token.startsWith(base)) hit = true;
  }
  return { hit, exact: false };
}

type Entry = {
  key: string;
  label: string;
  context: string;
  href: string;
  icon?: ReactNode;
  labelTokens: string[];
  allTokens: string[];
};

function buildEntries(sections: NavSection[]): Entry[] {
  const seen = new Set<string>();
  const entries: Entry[] = [];
  for (const { section, group, link } of flattenNav(sections)) {
    if (seen.has(link.href)) continue;
    seen.add(link.href);
    const context = [section.label, group.title].filter(Boolean).join(" · ");
    entries.push({
      key: `${section.key}:${group.key}:${link.key}`,
      label: link.label,
      context,
      href: link.href,
      icon: link.icon,
      labelTokens: tokens(link.label),
      allTokens: tokens([link.label, section.label, group.title ?? "", ...(section.keywords ?? []), ...(link.keywords ?? [])].join(" ")),
    });
  }
  return entries;
}

const LOCATION_PAGE_ENTRIES = LOCATION_PAGES.filter((page) => page.suffix !== "").map((page) => ({
  page,
  labelTokens: tokens(page.label),
  allTokens: tokens([page.label, ...page.keywords].join(" ")),
}));

export type PageSearchPlan = {
  /** Что отправить на сервер (локации и люди). Пусто — сервер не нужен. */
  serverQuery: string;
  pages: PageHit[];
  /** Страницы локации, которые узнались в запросе, — для склейки с локациями. */
  locationPages: typeof LOCATION_PAGES[number][];
};

export function planSearch(raw: string, sections: NavSection[]): PageSearchPlan {
  const words = tokens(raw);
  if (words.length === 0) return { serverQuery: "", pages: [], locationPages: [] };

  const hits: PageHit[] = [];
  for (const entry of buildEntries(sections)) {
    let score = 0;
    let all = true;
    for (const word of words) {
      const inLabel = wordMatches(word, entry.labelTokens);
      const inAny = inLabel.hit ? inLabel : wordMatches(word, entry.allTokens);
      if (!inAny.hit) {
        all = false;
        break;
      }
      score += (inLabel.hit ? 3 : 1) + (inAny.exact ? 1 : 0);
    }
    if (all) {
      hits.push({ key: entry.key, label: entry.label, context: entry.context, href: entry.href, icon: entry.icon, score });
    }
  }
  hits.sort((a, b) => b.score - a.score || a.label.localeCompare(b.label, "ru"));

  // Слова, узнанные как страница локации, в запрос локаций не идут: иначе
  // «погода сокол» искал бы локацию со словом «погода» в названии.
  const locationPages: typeof LOCATION_PAGES[number][] = [];
  const pageWords = new Set<string>();
  for (const { page, allTokens } of LOCATION_PAGE_ENTRIES) {
    const matched = words.filter((word) => word.length >= 3 && wordMatches(word, allTokens).hit);
    if (matched.length > 0) {
      locationPages.push(page);
      matched.forEach((word) => pageWords.add(word));
    }
  }
  const rest = words.filter((word) => !pageWords.has(word));
  const serverQuery = rest.length > 0 && pageWords.size > 0 ? rest.join(" ") : words.join(" ");

  return {
    serverQuery,
    pages: hits.slice(0, 6),
    locationPages: rest.length > 0 ? locationPages : [],
  };
}

/** «Погода · Сокольники»: страница локации, склеенная с найденной локацией. */
export function combineLocationPages(
  plan: PageSearchPlan,
  locations: SiteSearchLocation[],
): PageHit[] {
  const out: PageHit[] = [];
  for (const location of locations.slice(0, 3)) {
    for (const page of plan.locationPages) {
      out.push({
        key: `combo:${location.slug}:${page.key}`,
        label: `${page.label} · ${location.name}`,
        context: location.city ? `Локация · ${location.city}` : "Локация",
        href: `${location.href}${page.suffix}`,
        icon: page.icon,
        score: 100,
      });
    }
  }
  return out.slice(0, 4);
}
