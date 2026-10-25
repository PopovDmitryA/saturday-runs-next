import { PORTAL_REGISTRATION_HREF } from "./portalRoutes";

/**
 * Регистрация в беговых системах: куда вести человека без профиля системы.
 *
 * Регистрация проходит только на сайте самой системы. Свою форму с пробросом
 * данных Дмитрий отложил (исследование 23.10.2026, решение 25.10.2026):
 * условия 5 вёрст запрещают чужую форму, формы S95 и RunPark закрыты защитой
 * от подделки запросов. Поэтому сайт даёт ссылку и подсказку «после
 * регистрации привяжите профиль здесь».
 *
 * Метка у ссылки одна на всех — utm_source=run5k и место в utm_medium. Её
 * видит Метрика системы; персонального идентификатора в адресе нет (это была
 * бы передача данных наших пользователей). Сами клики сайт считает в канале
 * "signup" (ab_events), сводка — в админке «Популярность».
 */
export type SignupPlatformCode = "five_verst" | "s95" | "runpark" | "parkrun";

export const SIGNUP_PLATFORMS: readonly SignupPlatformCode[] = ["five_verst", "s95", "runpark", "parkrun"];

const SIGNUP_URLS: Record<SignupPlatformCode, string> = {
  five_verst: "https://5verst.ru/register/",
  s95: "https://s95.ru/user/sign_up",
  runpark: "https://runpark.ru/Auth/Register",
  parkrun: "https://www.parkrun.org.uk/register/",
};

/**
 * Сайты S95 по странам. Аккаунт S95 принадлежит стране того сайта, на котором
 * человек зарегистрировался, поэтому со страницы белорусской локации ведём на
 * s95.by, а не на s95.ru.
 */
export const S95_SIGNUP_SITES: readonly { domain: string; country: string }[] = [
  { domain: "s95.ru", country: "Россия" },
  { domain: "s95.by", country: "Беларусь" },
  { domain: "s95.kz", country: "Казахстан" },
  { domain: "s95.rs", country: "Сербия" },
];

/** Как привязать профиль у нас — коротко, для карточек и плиток. */
export const SIGNUP_LINK_HOWTO: Record<SignupPlatformCode, string> = {
  five_verst: "вставьте в поиск номер A790… или ссылку на профиль 5verst.ru/userstats/…",
  s95: "вставьте в поиск ссылку на профиль s95.ru/athletes/… или штрихкод A770…",
  runpark: "привязать профиль можно только после первой пробежки — найдите себя по штрихкоду",
  // По имени не найти: мировые протоколы parkrun в базу сайта не переносятся.
  parkrun: "вставьте ссылку на профиль parkrun после первого старта",
};

export function isSignupPlatform(code: string): code is SignupPlatformCode {
  return (SIGNUP_PLATFORMS as readonly string[]).includes(code);
}

/** Домен S95 из адреса локации («https://s95.by/events/…» → «s95.by»). */
export function s95DomainFromUrl(url: string | null | undefined): string | null {
  if (!url) {
    return null;
  }
  try {
    const host = new URL(url).hostname.replace(/^www\./, "");
    return S95_SIGNUP_SITES.some((site) => site.domain === host) ? host : null;
  } catch {
    return null;
  }
}

/**
 * Адрес формы регистрации системы с нашей общей меткой. place — где нажали:
 * page (страница регистрации, "page:<откуда>"), onboarding, cabinet, location,
 * teaser; те же значения уходят в событие "<система>:<место>".
 */
export function signupUrl(
  code: SignupPlatformCode,
  place: string,
  options: { s95Domain?: string | null } = {},
): string {
  const url = new URL(SIGNUP_URLS[code]);
  if (code === "s95" && options.s95Domain && S95_SIGNUP_SITES.some((site) => site.domain === options.s95Domain)) {
    url.hostname = options.s95Domain;
  }
  url.searchParams.set("utm_source", "run5k");
  url.searchParams.set("utm_medium", place);
  url.searchParams.set("utm_campaign", "signup");
  return url.toString();
}

/**
 * Строки /registration и их вид (решения Дмитрия 25.10.2026): страница
 * подстраивается под то, что человек хочет зарегистрировать.
 *
 * - Пришёл за конкретной системой (точка карты, страница локации, карточка в
 *   кабинете или на главной — система в якоре адреса): она основная и первая,
 *   остальные — серые.
 * - Вошёл без такой подсказки: непривязанные — основные.
 * - Привязанные — всегда серые, с отметкой «привязан» и в самом конце:
 *   регистрация по ним человеку не интересна.
 * - Все четыре основными — только гостю, о котором ничего не известно.
 *
 * linkedCodes = null — гость. Целевая система, которая уже привязана, — не цель.
 */
export type RegistrationRowTone = "main" | "muted" | "linked";

export type RegistrationRow = { code: SignupPlatformCode; tone: RegistrationRowTone };

export function registrationRows(
  target: SignupPlatformCode | null,
  linkedCodes: readonly string[] | null,
): RegistrationRow[] {
  const linkedSet = new Set(linkedCodes ?? []);
  const goal = target && !linkedSet.has(target) ? target : null;
  const rows: RegistrationRow[] = goal ? [{ code: goal, tone: "main" }] : [];
  for (const code of SIGNUP_PLATFORMS) {
    if (code !== goal && !linkedSet.has(code)) {
      rows.push({ code, tone: goal ? "muted" : "main" });
    }
  }
  for (const code of SIGNUP_PLATFORMS) {
    if (linkedSet.has(code)) {
      rows.push({ code, tone: "linked" });
    }
  }
  return rows;
}

/**
 * Наша страница регистрации — сюда ведут ВСЕ призывы сайта, а уже с неё
 * человек уходит на сайт системы (решение Дмитрия 25.10.2026). code — какая
 * система будет основной; from — откуда пришли: клик по ссылке системы на
 * странице запишется как "page:<from>"; s95Domain — сайт С95 страны локации
 * (аккаунт принадлежит стране сайта регистрации).
 */
export function registrationPageHref(
  code?: SignupPlatformCode | null,
  from?: string,
  s95Domain?: string | null,
): string {
  const params = new URLSearchParams();
  if (from) {
    params.set("from", from);
  }
  if (code === "s95" && s95Domain && s95Domain !== "s95.ru") {
    params.set("s95", s95Domain);
  }
  const query = params.toString();
  return `${PORTAL_REGISTRATION_HREF}${query ? `?${query}` : ""}${code ? `#${code}` : ""}`;
}
