/**
 * Личное, что сайт держит в этом браузере, — и как это забыть.
 *
 * Два уровня:
 * - forgetSessionBrowserState — выход из профиля (LogoutButton): всё, что
 *   следующий вошедший в этом браузере не должен увидеть чужим. Профиль
 *   остаётся, поэтому удобства вроде недавних локаций живут дальше;
 * - forgetPersonalBrowserState — профиль удалён: стираем и следы самого
 *   человека (недавние локации, скрытые плашки, «с возвращением» на входе).
 *
 * Тема, свёрнутая колонка, вид таблиц и анонимный id посетителя
 * (sr_visitor_id — счётчик уников без привязки к профилю) — настройки
 * браузера, а не человека: их не трогаем.
 */
import { forgetOrganizer } from "../features/portal/nav/organizerMemory";
import { forgetSearchClaim } from "../features/portal/nav/searchClaim";
import { forgetEntryMemory } from "./entryMemory";
import { forgetLoginNext } from "./loginReturn";
import { forgetSignupIntent } from "./signupIntent";
import { clearCachedUser } from "./useOptionalUser";

// Ключи, у владельцев которых нет своей функции «забыть». Имя — ровно как в
// модуле-владельце; переименовали там — поправьте здесь. Ключ, за которым
// стоит хранилище в памяти, сюда не годится: владелец перезапишет его на
// pagehide (так было со снимками страниц, lib/entryMemory.ts), — ему нужна
// своя функция «забыть».
const PERSONAL_LOCAL_KEYS = [
  "sr_teaser_claim", // features/portal/teaserClaim.tsx — «это я» с главной до входа
  "portalReturningUser", // features/portal/PortalLoginPage.tsx — «с возвращением»
  "recentLocations:v1", // lib/recentLocations.ts
  "map_geolocation_asked", // lib/mapGeolocation.ts
  "map_geo_ping_sent_on", // lib/mapGeolocation.ts
  "onThisDayDismissedDate", // components/OnThisDayCard.tsx
  "recentRatingsDismissed", // lib/ratingDismissals.ts — какие старты не предлагать оценить
];
const PERSONAL_LOCAL_PREFIXES = [
  "srs.dismissed.", // lib/weeklyDismissal.ts — плашки, закрытые до субботы
  "sr_funnel_auth_done:", // lib/abTest.ts — в ключе id профиля
];
const PERSONAL_SESSION_KEYS = [
  "notify-prompt-snoozed", // components/NotificationsPromptModal.tsx
  "lastLocationHint", // lib/locationHint.ts
  "portalPendingLoginEmail", // features/portal/PortalLoginPage.tsx — адрес почты
];

// Кука «это устройство уже входило» (features/portal/PortalLoginPage.tsx): по
// ней экран входа считает согласие данным. В ответе на удаление её снимает
// сервер, но ответ мог не дойти — связь оборвалась после commit, и об
// удалении DeleteAccountSection узнал по id попытки (GET
// /account/deletion-status). Тогда кука осталась бы, а согласия больше нет ни
// в одном профиле. Не httpOnly — стираем отсюда.
const KNOWN_DEVICE_COOKIE = "sr_known";

function removeKeys(storage: Storage, keys: string[], prefixes: string[] = []): void {
  for (const key of keys) {
    storage.removeItem(key);
  }
  if (!prefixes.length) {
    return;
  }
  // Сначала собираем, потом удаляем: удаление сдвигает индексы key(i).
  const matched: string[] = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (key && prefixes.some((prefix) => key.startsWith(prefix))) {
      matched.push(key);
    }
  }
  for (const key of matched) {
    storage.removeItem(key);
  }
}

/** Выход из профиля: кэш пользователя и ответов API, роль организатора,
 * отложенное «Это вы?» из поиска и адрес возврата после входа. */
export function forgetSessionBrowserState(): void {
  clearCachedUser();
  forgetOrganizer();
  forgetSearchClaim();
  forgetSignupIntent();
  forgetLoginNext();
}

/** Профиль удалён: всё из forgetSessionBrowserState и личные следы человека. */
export function forgetPersonalBrowserState(): void {
  forgetSessionBrowserState();
  forgetEntryMemory();
  try {
    document.cookie = `${KNOWN_DEVICE_COOKIE}=; Max-Age=0; Path=/; SameSite=Lax`;
  } catch {
    // документ без кук (песочница) — стирать нечего
  }
  try {
    removeKeys(window.localStorage, PERSONAL_LOCAL_KEYS, PERSONAL_LOCAL_PREFIXES);
  } catch {
    // хранилище недоступно (приватный режим) — и забывать нечего
  }
  try {
    removeKeys(window.sessionStorage, PERSONAL_SESSION_KEYS);
  } catch {
    // см. выше
  }
}
