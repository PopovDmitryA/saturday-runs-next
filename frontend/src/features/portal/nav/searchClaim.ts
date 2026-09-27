/**
 * «Это вы?» из поиска → вход → привязка именно этого человека (решение
 * Дмитрия 27.09.2026).
 *
 * Гость нашёл себя в протоколах и нажал «Войти и привязать». Вход уводит его
 * к VK, Яндексу или в Telegram, а назад провайдер возвращает на свой
 * redirect_uri — наши параметры адреса до него не доезжают. Поэтому намерение
 * лежит в localStorage (как у тизера главной, teaserClaim.tsx), а после входа
 * его подбирает SearchClaimRunner и спрашивает «Это вы? — Это я, привязать» с
 * тем же человеком: выбирать заново среди однофамильцев не нужно.
 *
 * В отличие от тизера намерение не гасится при чтении: привязать молча нельзя,
 * удаляем только после ответа человека (или окончательного отказа сервера).
 * Рядом с токеном — снимок карточки: после перезагрузки её есть чем нарисовать
 * и на странице входа, и если сервер ответит «результаты поиска устарели».
 */
import type { SearchClaimPerson } from "../../../lib/api";

const CLAIM_KEY = "sr_search_claim";

// Столько же живёт токен на сервере (SEARCH_CLAIM_TTL_SECONDS): вход по почте
// идёт до 10 минут, плюс запас. Дольше — это уже другой заход, и за общим
// компьютером следующий вошедший не должен увидеть чужое «Это вы?».
const CLAIM_TTL_MS = 2 * 60 * 60 * 1000;

export type PendingSearchClaim = {
  token: string;
  snapshot: SearchClaimPerson;
  saved_at: number;
};

export function rememberSearchClaim(token: string, snapshot: SearchClaimPerson): void {
  try {
    const payload: PendingSearchClaim = { token, snapshot, saved_at: Date.now() };
    localStorage.setItem(CLAIM_KEY, JSON.stringify(payload));
  } catch {
    // Приватный режим и переполненное хранилище — вход всё равно состоится,
    // просто после него человек привяжет себя сам.
  }
}

export function forgetSearchClaim(): void {
  try {
    localStorage.removeItem(CLAIM_KEY);
  } catch {
    // хранилище недоступно — и забывать нечего
  }
}

function isSnapshot(value: unknown): value is SearchClaimPerson {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return (
    typeof item.display_name === "string" &&
    typeof item.platform_code === "string" &&
    typeof item.total_runs === "number" &&
    typeof item.total_volunteering === "number"
  );
}

/** Отложенное намерение без удаления; просроченное или битое — стирает. */
export function peekSearchClaim(): PendingSearchClaim | null {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(CLAIM_KEY);
  } catch {
    return null;
  }
  if (raw === null) return null;
  let parsed: PendingSearchClaim | null = null;
  try {
    const value = JSON.parse(raw) as Partial<PendingSearchClaim> | null;
    if (
      value &&
      typeof value.token === "string" &&
      value.token &&
      typeof value.saved_at === "number" &&
      isSnapshot(value.snapshot) &&
      Date.now() - value.saved_at <= CLAIM_TTL_MS
    ) {
      parsed = value as PendingSearchClaim;
    }
  } catch {
    parsed = null;
  }
  if (parsed === null) forgetSearchClaim();
  return parsed;
}

export function hasPendingSearchClaim(): boolean {
  return peekSearchClaim() !== null;
}

// Тексты исходов — одни и те же в подэкране поиска и в окне после входа:
// человек может привязать один профиль там, другой здесь, и прочитать об
// одном и том же одинаково.
export const CLAIM_LINKED_TITLE = "Профиль привязан";
export const CLAIM_LINKED_TEXT = "Статистика появится в кабинете через пару минут.";
export const CLAIM_ALREADY_YOURS_TEXT = "Это ваш профиль — он уже привязан к вашему аккаунту.";

export function claimPlatformLinkedText(system: string): string {
  return `К вашему аккаунту уже привязан другой профиль ${system}. От каждой системы привязывается один профиль.`;
}

/**
 * «Занят» чаще всего значит, что человек уже заводил аккаунт и вошёл сейчас
 * другим способом (закрытый профиль в выдаче — обычная строка из протоколов).
 * Без подсказки он остаётся в новом пустом кабинете и не понимает почему.
 */
export const CLAIM_TAKEN_TEXT = "Этот профиль уже привязан к другому аккаунту.";
export const CLAIM_TAKEN_HINT =
  "Если это вы — похоже, раньше вы входили на сайт другим способом (Telegram, VK, Яндекс или почта). " +
  "Войдите им — или добавьте его в настройках, в «Способах входа»: там же предложим объединить аккаунты.";
