/**
 * Поиск по страницам сайта — на клиенте, по дереву siteNav и его синонимам.
 *
 * Сервер про страницы ничего не знает: дерево навигации живёт во фронте, и
 * держать второй список на бэкенде значило бы снова завести два меню, которые
 * разъедутся. Сервер ищет локации и людей, а страницы находятся здесь.
 *
 * Как спрашивают на самом деле (ревью 25.09.2026): вопросом («где мой
 * протокол», «с кем я бегал»), одним словом («погода», «топ»), с «мой» и
 * «как». Поэтому:
 * - служебные слова («как», «где», «мой», «в», «найти»…) выбрасываются —
 *   раньше одно «мой» находило Самойловых, а «где» обнуляло страницы;
 * - для страницы хватает совпадения большинства значимых слов, а не всех;
 * - слово, узнанное как страница («погода», «рейтинги», «войти»), в поиск
 *   людей не уходит: «погода» больше не выдаёт Погодаевых;
 * - страница локации без названия локации («погода», «протокол», «топ»)
 *   предлагается для текущей, своей и недавней локации (см. placeless);
 * - при равных баллах выше то, что чаще открывают (PAGE_WEIGHTS).
 */
import type { ReactNode } from "react";
import type { SiteSearchLocation } from "../../../lib/api";
import { PORTAL_LOGIN_HREF } from "../../../lib/portalRoutes";
import * as I from "./navIcons";
import {
  EXTRA_SEARCH_LINKS,
  LOCATION_PAGES,
  flattenNav,
  type NavLink,
  type NavPlace,
  type NavSection,
} from "./siteNav";

export type PageHitKind = "page" | "login" | "place-page";

export type PageHit = {
  key: string;
  label: string;
  /** Где страница живёт: «Рейтинги · Туристы», «Кабинет». */
  context: string;
  href: string;
  icon?: ReactNode;
  score: number;
  kind: PageHitKind;
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

/** Слова исходного запроса — как набрал человек (регистр и «ё» на месте). */
function rawTokens(raw: string): string[] {
  return raw
    .replace(/[^\p{L}\p{N}\s-]/gu, " ")
    .split(/[\s-]+/)
    .filter(Boolean);
}

/**
 * Служебные слова: вопросы, предлоги, «найти», «страница». Ни страницу, ни
 * человека они не называют, а мешали и тем и другим.
 */
const STOP_WORDS = new Set(
  [
    "как", "где", "что", "кто", "какой", "какая", "какие", "когда", "почему", "зачем", "куда", "откуда", "сколько", "чей",
    "кем", "чем", "кого", "чего", "кому", "чему", "ком", "мы", "нас", "нам", "мной",
    "найти", "найди", "найдите", "посмотреть", "смотреть", "показать", "покажи", "открыть", "перейти", "хочу", "можно",
    "нужно", "надо", "узнать", "есть", "это", "там", "тут", "здесь", "все", "всех", "весь", "вся",
    "в", "во", "на", "по", "для", "с", "со", "у", "о", "об", "из", "от", "до", "за", "к", "ко", "и", "или", "а", "же",
    "ли", "не", "бы", "страница", "страницу", "страницы", "раздел", "сайт", "сайте",
  ].map((word) => normalizeQuery(word)),
);

/**
 * «Мой», «я», «себя» — тоже служебные для сравнения, но говорят, что человек
 * ищет СВОЁ: вошедшему поднимаем страницы кабинета, гостю — вход.
 */
const PERSONAL_WORDS = new Set(
  ["мой", "моя", "мои", "моё", "моего", "моей", "моих", "моим", "мне", "меня", "я", "свой", "своя", "свои", "своё", "себя"].map(
    (word) => normalizeQuery(word),
  ),
);

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

// Падежные окончания: «погоду», «протоколах», «топы». «-ов» и «-ин» сюда не
// входят нарочно — иначе «Морозов» и «Погодин» считались бы словами-страницами
// («мороз», «погода») и не доходили бы до поиска людей.
const CASE_ENDINGS = ["", "а", "я", "у", "ю", "е", "ы", "и", "ой", "ей", "ам", "ям", "ах", "ях", "ами", "ями", "ом", "ем"];
const VOWELS = new Set(["а", "я", "о", "е", "ы", "и", "у", "ю", "ь", "й"]);

/** word — то же слово, что token, в другом падеже: «карту» — «карта», «клубы» — «клуб». */
function isCaseForm(word: string, token: string): boolean {
  if (token.length < 3) return false;
  const bases = VOWELS.has(token[token.length - 1]) ? [token, token.slice(0, -1)] : [token];
  return bases.some((base) => word.startsWith(base) && CASE_ENDINGS.includes(word.slice(base.length)));
}

const DIGITS = /^\d+$/;
// Короче пяти букв основа ловит чужие слова: «вёрст» → «верс» → «версия»
// (и «5 вёрст» находил «Обновления»), «бегал» → «бега».
const MIN_STEM = 5;

function wordMatches(word: string, haystack: string[]): { hit: boolean; exact: boolean } {
  // Цифры — только целиком: «5» не должно находить «50 стартов».
  const digits = DIGITS.test(word);
  const base = stem(word);
  const byStem = !digits && base.length >= MIN_STEM;
  let hit = false;
  for (const token of haystack) {
    if (token === word) return { hit: true, exact: true };
    if (digits) continue;
    // Начало слова (человек ещё печатает), основа длинного слова или то же
    // слово в другом падеже.
    if (token.startsWith(word) || (byStem && token.startsWith(base)) || isCaseForm(word, token)) hit = true;
  }
  return { hit, exact: false };
}

/**
 * Слово точно называет страницу: совпадает со словом названия или синонима
 * с точностью до падежа. Строже, чем wordMatches: такие слова не уходят в
 * поиск людей, и ошибка здесь прятала бы живых людей по фамилии.
 */
function namesPage(word: string, haystack: string[]): boolean {
  return haystack.some((token) => token === word || isCaseForm(word, token));
}

/**
 * Просмотры за 30 дней (прод, сентябрь 2026) — чем чаще страницу открывают,
 * тем выше она при равных баллах. Раньше при ничьей шёл алфавит: «топ»
 * первым выдавал «Волонтёрство на разных локациях», «итоги» — «Единый
 * протокол» (457) выше «Последних пробежек» (658).
 */
const PAGE_WEIGHTS: Record<string, number> = {
  "home:home": 1993,
  "me:dashboard": 900,
  "me:achievements": 120,
  "me:runs": 110,
  "me:map": 60,
  "me:share": 40,
  "me:history": 40,
  "me:volunteering": 30,
  "me:meetings": 20,
  "organizer:hub": 1200,
  "organizer:index": 500,
  "organizer:report": 400,
  "organizer:post": 300,
  "organizer:protocols": 250,
  "results:last-results": 658,
  "results:unified-protocol": 457,
  "locations:catalog": 1407,
  "locations:events": 1103,
  "locations:weather": 300,
  "locations:tops": 300,
  "locations:participants": 250,
  "ratings:hub": 1392,
  "ratings:locations": 398,
  "ratings:runs": 256,
  "ratings:volunteer-locations": 231,
  "ratings:volunteering": 130,
  "ratings:home-distance": 125,
  "ratings:location-records": 110,
  "ratings:wins": 93,
  "ratings:fastest": 90,
  "ratings:openings": 64,
  "ratings:regions": 55,
  "ratings:win-locations": 51,
  "ratings:volunteer-roles": 40,
  "project:about": 155,
  "project:backlog": 59,
  "project:updates": 39,
  "project:blog": 20,
  "account:settings": 109,
};

/**
 * Синонимы только для поиска — их нет в меню, но так спрашивают: «5 вёрст»,
 * «паркран» ведут в каталог локаций.
 */
const SEARCH_ONLY_KEYWORDS: Record<string, readonly string[]> = {
  "/locations": ["5 верст", "пять верст", "s95", "с95", "parkrun", "паркран", "runpark", "ранпарк", "все локации"],
};

/** Гостю: вход и «найти себя». У гостя в разделе кабинета страниц нет — без этой строки «войти» находил Войтиковых. */
const LOGIN_LINK: NavLink = {
  key: "login",
  label: "Войти и найти себя",
  icon: I.ME_ICON,
  href: PORTAL_LOGIN_HREF,
  keywords: [
    "войти",
    "вход",
    "логин",
    "регистрация",
    "зарегистрироваться",
    "регистрироваться",
    "аккаунт",
    "привязать профиль",
    "привязать",
    "привязка",
    "найти себя",
    "кабинет",
    "личный кабинет",
    "мой результат",
    "мои результаты",
    "моя статистика",
  ],
};
const LOGIN_CONTEXT = "Пробежки, рекорды и карта — уже посчитаны";

type Entry = {
  key: string;
  weightKey: string;
  label: string;
  context: string;
  href: string;
  icon?: ReactNode;
  kind: PageHitKind;
  sectionKey: string;
  labelTokens: string[];
  /** Значимые слова названия — для «полного совпадения с названием». */
  labelCore: string[];
  /** Синонимы целиком: «с кем бегал» — это «Встречи», а не «Карта». */
  keywordPhrases: string[];
  keywordTokens: string[];
  contextTokens: string[];
  /**
   * Слова, которые делают слово запроса «словом-страницей» (и не пускают его
   * в поиск людей). У ссылок на конкретную локацию («Моя: Мещерский»,
   * недавние, страницы открытой локации) и у инструментов чужой локации в
   * названии стоит имя парка — его нельзя отнимать у поиска локаций и людей.
   */
  pageWordTokens: string[];
  personalKeywords: boolean;
  /**
   * Инструмент локации организатора: находится только по своему слову
   * («юбилеи», «пост»), а не по одному названию парка — иначе «сокольники»
   * у организатора выдавали бы дюжину инструментов вместо самой локации. И
   * только когда совпали ВСЕ значимые слова: «протоколы сокольники» не
   * должны находить «Протоколы · Кузьминки» по одному слову «протоколы».
   */
  needsStrong: boolean;
};

function makeEntry(
  link: NavLink,
  opts: {
    key: string;
    weightKey: string;
    context: string;
    sectionKey: string;
    contextWords: string[];
    kind?: PageHitKind;
    /**
     * Название локации в подписи («Календарь юбилеев · Сокольники»): по нему
     * страница находится слабо (как по разделу), и словом-страницей оно не
     * считается — его ищут и локации, и люди.
     */
    placeName?: string;
  },
): Entry {
  const placeTokens = opts.placeName ? tokens(opts.placeName) : [];
  const labelTokens = tokens(link.label).filter((token) => !placeTokens.includes(token));
  const keywordText = [...(link.keywords ?? []), ...(SEARCH_ONLY_KEYWORDS[link.href] ?? [])].join(" ");
  const keywordTokens = tokens(keywordText);
  const contextTokens = [...tokens(opts.contextWords.join(" ")), ...placeTokens];
  const placeLink = link.href.startsWith("/locations/");
  // У инструмента локации организатора имя парка из названия уже вычтено
  // (labelTokens) — «Календарь юбилеев» словом-страницей быть может, а
  // «Сокольники» остаются поиску локаций и людей.
  const pageWordTokens = placeLink
    ? []
    : opts.placeName !== undefined
      ? [...labelTokens, ...keywordTokens]
      : [...labelTokens, ...keywordTokens, ...contextTokens];
  return {
    key: opts.key,
    weightKey: opts.weightKey,
    label: link.label,
    context: opts.context,
    href: link.href,
    icon: link.icon,
    kind: opts.kind ?? "page",
    sectionKey: opts.sectionKey,
    labelTokens,
    labelCore: labelTokens.filter((token) => !STOP_WORDS.has(token) && !PERSONAL_WORDS.has(token)),
    keywordPhrases: (link.keywords ?? []).map((keyword) => tokens(keyword).filter((token) => !PERSONAL_WORDS.has(token)).join(" ")),
    keywordTokens,
    contextTokens,
    pageWordTokens,
    personalKeywords: keywordTokens.some((token) => PERSONAL_WORDS.has(token)),
    needsStrong: opts.placeName !== undefined,
  };
}

export type SearchContext = {
  /** Гость (не вошёл): ему строка «Войти и найти себя». */
  guest: boolean;
  /**
   * Дополнительные страницы: инструменты организатора всех его локаций.
   * placeName — локация, чьё имя дописано в подпись.
   */
  extraLinks?: readonly { link: NavLink; context: string; weightKey: string; placeName?: string }[];
};

function buildEntries(sections: NavSection[], ctx: SearchContext): Entry[] {
  const seen = new Set<string>();
  const entries: Entry[] = [];
  const push = (entry: Entry) => {
    if (seen.has(entry.href)) return;
    seen.add(entry.href);
    entries.push(entry);
  };
  if (ctx.guest) {
    push(makeEntry(LOGIN_LINK, { key: "extra:login", weightKey: "extra:login", context: LOGIN_CONTEXT, sectionKey: "me", contextWords: [], kind: "login" }));
  }
  // Инструменты организатора — раньше дерева: у инструментов открытой сейчас
  // локации тот же адрес, и побеждает запись, которая знает свою локацию
  // (placeName) и не находится по названию чужой.
  for (const extra of ctx.extraLinks ?? []) {
    push(
      makeEntry(extra.link, {
        key: `org:${extra.link.href}`,
        weightKey: extra.weightKey,
        context: extra.context,
        sectionKey: "organizer",
        contextWords: [extra.context],
        placeName: extra.placeName ?? "",
      }),
    );
  }
  for (const { section, group, link } of flattenNav(sections)) {
    const context = [section.label, group.title].filter(Boolean).join(" · ");
    push(
      makeEntry(link, {
        key: `${section.key}:${group.key}:${link.key}`,
        weightKey: `${section.key}:${link.key}`,
        context,
        sectionKey: section.key,
        contextWords: [section.label, group.title ?? "", ...(section.keywords ?? [])],
      }),
    );
  }
  for (const link of EXTRA_SEARCH_LINKS) {
    push(makeEntry(link, { key: `extra:${link.key}`, weightKey: `extra:${link.key}`, context: "run5k.run", sectionKey: "extra", contextWords: [] }));
  }
  return entries;
}

// У обзора локации название «Обзор» — общее слово (так же зовётся обзор
// кабинета), поэтому узнаём его только по синонимам: «как добраться», «трасса».
const LOCATION_PAGE_ENTRIES = LOCATION_PAGES.map((page) => ({
  page,
  allTokens: tokens([page.suffix === "" ? "" : page.label, ...page.keywords].join(" ")),
}));

export type LocationPageDef = (typeof LOCATION_PAGES)[number];

export type PageSearchPlan = {
  /** Что отправить на сервер (локации и люди). Пусто — сервер не нужен. */
  serverQuery: string;
  /** Сколько значимых слов ушло на сервер — от этого зависит подсказка про людей. */
  serverWords: number;
  pages: PageHit[];
  /** Страницы локации, которые узнались в запросе, — для склейки с локациями. */
  locationPages: LocationPageDef[];
  /**
   * Страница локации без названия локации («погода», «протокол»): её
   * предлагаем для текущей, своей и недавней локации.
   */
  placeless: boolean;
  /**
   * Как человек назвал страницу локации — для подсказки «допишите название»:
   * фраза целиком («самые быстрые», «как добраться») или синоним в
   * начальной форме («погоду» → «погода»).
   */
  placelessPhrase: string;
  /** В запросе «мой», «я», «себя»: человек ищет своё. */
  personal: boolean;
};

const EMPTY_PLAN: PageSearchPlan = {
  serverQuery: "",
  serverWords: 0,
  pages: [],
  locationPages: [],
  placeless: false,
  placelessPhrase: "",
  personal: false,
};

/** Как в запросе названа страница локации — см. PageSearchPlan.placelessPhrase. */
function locationPagePhrase(page: LocationPageDef, words: string[], named: string[]): string {
  // У «Обзора» название — общее слово, как и в поиске: только синонимы.
  const phrases = [...(page.suffix === "" ? [] : [page.label]), ...page.keywords].map(normalizeQuery).filter(Boolean);
  const whole = phrases
    .filter((phrase) => phrase.split(" ").every((token) => words.includes(token)))
    .sort((a, b) => b.split(" ").length - a.split(" ").length)[0];
  if (whole) return whole;
  for (const word of named) {
    const single = phrases.find((phrase) => !phrase.includes(" ") && namesPage(word, [phrase]));
    if (single) return single;
  }
  return named.join(" ");
}

export function planSearch(raw: string, sections: NavSection[], ctx: SearchContext): PageSearchPlan {
  const words = tokens(raw);
  if (words.length === 0) return EMPTY_PLAN;
  const original = rawTokens(raw);
  const aligned = original.length === words.length;

  const personal = words.some((word) => PERSONAL_WORDS.has(word));
  const significant = words.filter((word) => !STOP_WORDS.has(word) && !PERSONAL_WORDS.has(word));
  // Запрос целиком, без «мой» и «я»: «с кем я бегал» → «с кем бегал».
  const phrase = words.filter((word) => !PERSONAL_WORDS.has(word)).join(" ");
  const entries = buildEntries(sections, ctx);

  // ---- страницы дерева ----
  const hits: (PageHit & { weight: number })[] = [];
  const pageWords = new Set<string>();
  for (const entry of entries) {
    let score = 0;
    let matched = 0;
    let strong = false;
    for (const word of significant) {
      const inLabel = wordMatches(word, entry.labelTokens);
      if (inLabel.hit) {
        score += 4 + (inLabel.exact ? 1 : 0);
        matched += 1;
        strong = true;
      } else {
        const inKeywords = wordMatches(word, entry.keywordTokens);
        if (inKeywords.hit) {
          score += 2 + (inKeywords.exact ? 1 : 0);
          matched += 1;
          strong = true;
        } else {
          const inContext = wordMatches(word, entry.contextTokens);
          if (inContext.hit) {
            score += 1 + (inContext.exact ? 1 : 0);
            matched += 1;
          }
        }
      }
      if (namesPage(word, entry.pageWordTokens)) {
        pageWords.add(word);
      }
    }
    // «Моё» без других слов: вошедшему — кабинет. Гостю с любым «мой …»
    // («мои пробежки», «с кем я бегал») — вход: своё откроется после него.
    const personalOnly =
      personal && ((significant.length === 0 && entry.sectionKey === "me") || entry.kind === "login");
    const enough =
      significant.length > 0 &&
      (entry.needsStrong
        ? strong && matched === significant.length
        : matched === significant.length || (strong && matched * 2 >= significant.length));
    if (!enough && !personalOnly) continue;
    if (personal && (entry.sectionKey === "me" || entry.personalKeywords)) score += 2;
    // Полное совпадение с названием страницы — первым: «самые быстрые» —
    // это рейтинг, а не «Топы бегунов» своей локации.
    if (entry.labelCore.length > 0 && entry.labelCore.every((token) => significant.includes(token))) score += 100;
    // Запрос слово в слово совпал с синонимом — служебные слова в нём
    // значимы: «с кем бегал» — «Встречи», хотя «бегал» есть и у «Карты».
    else if (phrase && entry.keywordPhrases.includes(phrase)) score += 10;
    // Гостю «мой результат», «найти себя», «войти» — сначала вход.
    if (entry.kind === "login" && (personal || strong)) score += 200;
    hits.push({
      key: entry.key,
      label: entry.label,
      context: entry.context,
      href: entry.href,
      icon: entry.icon,
      score,
      kind: entry.kind,
      weight: PAGE_WEIGHTS[entry.weightKey] ?? 0,
    });
  }
  hits.sort((a, b) => b.score - a.score || b.weight - a.weight || a.label.localeCompare(b.label, "ru"));

  // ---- страницы локации: «погода сокол» → «Погода · Сокольники» ----
  const locationPages: LocationPageDef[] = [];
  let placelessPhrase = "";
  for (const { page, allTokens } of LOCATION_PAGE_ENTRIES) {
    const named = significant.filter((word) => word.length >= 3 && namesPage(word, allTokens));
    if (named.length > 0) {
      locationPages.push(page);
      named.forEach((word) => pageWords.add(word));
      placelessPhrase ||= locationPagePhrase(page, words, named);
    }
  }

  // На сервер — только то, что не узнано как страница и не служебное.
  // Слова берём как набрал человек: сервер сам сравнивает без «ё» и регистра.
  const serverWords: string[] = [];
  words.forEach((word, index) => {
    if (STOP_WORDS.has(word) || PERSONAL_WORDS.has(word) || pageWords.has(word)) return;
    serverWords.push(aligned ? original[index] : word);
  });
  // Запрос из одних служебных слов — это, скорее всего, фамилия: «Ли» —
  // частица, но и корейская фамилия. Страниц у такого запроса нет, так что
  // пусть его посмотрит сервер.
  if (serverWords.length === 0 && significant.length === 0 && !personal && pageWords.size === 0) {
    words.forEach((word, index) => serverWords.push(aligned ? original[index] : word));
  }
  const placeless = locationPages.length > 0 && serverWords.length === 0;

  return {
    serverQuery: serverWords.join(" "),
    serverWords: serverWords.length,
    pages: hits.slice(0, 6).map(({ weight: _weight, ...hit }) => hit),
    locationPages,
    placeless,
    placelessPhrase,
    personal,
  };
}

/** «Погода · Сокольники»: страница локации, склеенная с найденной локацией. */
export function combineLocationPages(plan: PageSearchPlan, locations: SiteSearchLocation[]): PageHit[] {
  if (plan.placeless) return [];
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
        kind: "place-page",
      });
    }
  }
  return out.slice(0, 4);
}

export type KnownPlace = NavPlace & {
  /** Откуда мы знаем эту локацию: открыта сейчас, своя, недавно открывали. */
  why: "current" | "home" | "recent";
};

const PLACE_CONTEXT: Record<KnownPlace["why"], string> = {
  current: "Эта локация",
  home: "Моя локация",
  recent: "Недавно открывали",
};

/** «Погода» без названия локации — для текущей, своей и недавней. */
export function placelessLocationPages(plan: PageSearchPlan, places: KnownPlace[]): PageHit[] {
  if (!plan.placeless) return [];
  const out: PageHit[] = [];
  for (const page of plan.locationPages) {
    for (const place of places) {
      out.push({
        key: `place:${place.slug}:${page.key}`,
        // Имени ещё нет (страница локации не загрузилась) — без латиницы
        // адреса: подпись «Эта локация» стоит рядом.
        label: place.name ? `${page.label} · ${place.name}` : page.label,
        context: PLACE_CONTEXT[place.why],
        href: `/locations/${encodeURIComponent(place.slug)}${page.suffix}`,
        icon: page.icon,
        score: 50,
        kind: "place-page",
      });
    }
  }
  return out.slice(0, 4);
}

/**
 * Строки страниц в выдаче: склейки с локациями, потом страницы дерева без
 * повторов адреса. Полное совпадение с названием страницы (балл от 100)
 * стоит выше склеек без названия локации.
 */
export function mergePageHits(combos: PageHit[], pages: PageHit[], limit = 7): PageHit[] {
  const seen = new Set<string>();
  const out: PageHit[] = [];
  const ordered = [...combos, ...pages].sort((a, b) => {
    const top = (hit: PageHit) => (hit.kind === "login" || hit.score >= 100 ? 1 : 0);
    return top(b) - top(a);
  });
  for (const hit of ordered) {
    if (seen.has(hit.href)) continue;
    seen.add(hit.href);
    out.push(hit);
    if (out.length >= limit) break;
  }
  return out;
}
