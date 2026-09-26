/**
 * Что сайт помнит об организаторе между страницами и вкладками.
 *
 * Три вещи, все — в localStorage этого браузера и все стираются при выходе:
 * - признак «организатор». Кэш пользователя живёт в sessionStorage, то есть у
 *   каждой вкладки свой: в новой вкладке (переход из Telegram) рельс и нижняя
 *   панель сначала рисовались без «Оргкабинета», а через секунду, когда
 *   приезжал /auth/me, перестраивались — палец попадал не туда (a11y-6);
 * - последняя открытая организатором локация: «Оргкабинет» ведёт сразу в неё,
 *   а не на промежуточный список (code-8);
 * - короткий список его локаций (slug, имя, город): при одной локации
 *   «Оргкабинет» сразу ведёт в неё, «Мои локации» прячется, а переключатель
 *   в колонке не мигает, пока едет ответ.
 *
 * Личных данных тут нет — только роль и названия локаций.
 */
import type { User } from "../../../lib/api";

export type OrganizerPlaceItem = { slug: string; name: string; city: string | null };

type NavMemory = {
  userId: string;
  organizer: boolean;
  lastSlug?: string;
  lastName?: string;
};

type ListMemory = { userId: string; items: OrganizerPlaceItem[]; at: number };

const NAV_KEY = "organizerNav:v1";
const LIST_KEY = "organizerLocations:v2";

function readNav(): NavMemory | null {
  try {
    const raw = localStorage.getItem(NAV_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as NavMemory;
    return typeof parsed?.userId === "string" ? parsed : null;
  } catch {
    return null;
  }
}

function writeNav(value: NavMemory): void {
  try {
    localStorage.setItem(NAV_KEY, JSON.stringify(value));
  } catch {
    // приватный режим — просто не помним
  }
}

// Список у админа — весь каталог (десятки килобайт): разбираем JSON один раз
// на документ, а не на каждой перерисовке меню.
let listMemo: { raw: string; value: ListMemory | null } | null = null;

function readList(userId: string): ListMemory | null {
  try {
    const raw = localStorage.getItem(LIST_KEY);
    if (!raw) return null;
    if (listMemo?.raw !== raw) {
      const parsed = JSON.parse(raw) as ListMemory;
      listMemo = { raw, value: Array.isArray(parsed?.items) ? parsed : null };
    }
    const value = listMemo.value;
    return value && value.userId === userId ? value : null;
  } catch {
    return null;
  }
}

/** Роль стала известна (ответ /auth/me) — запомнить для следующих вкладок. */
export function rememberOrganizerRole(user: User | null): void {
  if (user === null) {
    // Гость — ничего от прошлого входа не показываем.
    forgetOrganizer();
    return;
  }
  const organizer = user.is_organizer || user.is_admin;
  const previous = readNav();
  if (previous && previous.userId === user.id && previous.organizer === organizer) return;
  writeNav(previous && previous.userId === user.id ? { ...previous, organizer } : { userId: user.id, organizer });
}

/** Пока /auth/me не ответил: был ли человек организатором в прошлый раз. */
export function rememberedOrganizerRole(): boolean {
  return readNav()?.organizer === true;
}

/** Организатор открыл кабинет локации — «Оргкабинет» дальше ведёт сюда. */
export function rememberOrganizerPlace(userId: string, place: { slug: string; name: string }): void {
  const previous = readNav();
  const base: NavMemory = previous && previous.userId === userId ? previous : { userId, organizer: true };
  // Имя до ответа API бывает равно slug — такое не запоминаем поверх настоящего.
  const name = place.name && place.name !== place.slug ? place.name : base.lastSlug === place.slug ? base.lastName : undefined;
  if (base.lastSlug === place.slug && base.lastName === name) return;
  writeNav({ ...base, lastSlug: place.slug, lastName: name });
}

export function readOrganizerLocations(userId: string): OrganizerPlaceItem[] | null {
  return readList(userId)?.items ?? null;
}

/** Когда список сохраняли в последний раз (мс), или null. */
export function organizerLocationsSavedAt(userId: string): number | null {
  return readList(userId)?.at ?? null;
}

export function writeOrganizerLocations(userId: string, items: OrganizerPlaceItem[]): void {
  try {
    localStorage.setItem(LIST_KEY, JSON.stringify({ userId, items, at: Date.now() } satisfies ListMemory));
  } catch {
    // переполнение или приватный режим — живём без кэша
  }
}

/**
 * Куда вести «Оргкабинет» вне кабинета организатора: последняя открытая
 * локация, а если её нет — единственная своя. null — нужен список (/organizer).
 */
export function organizerEntryPlace(user: User | null | undefined): { slug: string; name: string } | null {
  if (user === null) return null;
  const nav = readNav();
  if (user && nav && nav.userId !== user.id) return null;
  const userId = user?.id ?? nav?.userId;
  if (!userId) return null;
  const list = readOrganizerLocations(userId);
  if (nav?.lastSlug) {
    // Доступ к локации могли отозвать: если список есть и её там нет, забываем.
    const listed = list?.find((item) => item.slug === nav.lastSlug);
    if (!list || listed || user?.is_admin) {
      return { slug: nav.lastSlug, name: listed?.name ?? nav.lastName ?? nav.lastSlug };
    }
  }
  if (list && list.length === 1) {
    return { slug: list[0].slug, name: list[0].name };
  }
  return null;
}

/**
 * Есть ли у организатора выбор между локациями: больше одной своей или он
 * админ (у админа в кабинете весь каталог). Тогда «Мои локации» остаются в
 * дереве, даже когда «Оргкабинет» уже ведёт в запомненную локацию: иначе
 * список не находил поиск и не показывало «Меню» на телефоне (V9).
 */
export function organizerHasManyPlaces(user: User | null | undefined): boolean {
  if (user === null) return false;
  if (user?.is_admin) return true;
  // Пока сессия проверяется — по тому, кого браузер помнит организатором.
  const userId = user?.id ?? readNav()?.userId;
  if (!userId) return false;
  return (readOrganizerLocations(userId)?.length ?? 0) > 1;
}

/** Выход из аккаунта: роль, последняя локация и список — всё чужое следующему. */
export function forgetOrganizer(): void {
  try {
    localStorage.removeItem(NAV_KEY);
    localStorage.removeItem(LIST_KEY);
  } catch {
    // ignore
  }
  listMemo = null;
}
