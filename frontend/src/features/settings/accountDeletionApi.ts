// API удаления профиля. Отдельный модуль, а не lib/api.ts — тот файл
// одновременно правят другие фичи (паттерн features/backlog/backlogApi.ts).
import { ApiError } from "../../lib/api";

/** Что исчезнет вместе с профилем — цифры для окна подтверждения. */
export type AccountDeletionPreview = {
  platforms: string[];
  auth_providers: string[];
  location_reviews: number;
  review_photos: number;
  goals: number;
  /** Планы стартов и отметки «Не бегаю» вместе. */
  run_plans: number;
  /** Постоянные планы «каждую субботу», в том числе остановленные. */
  run_plan_series?: number;
  run_tracks: number;
  backlog_cards: number;
  backlog_comments: number;
  backlog_votes: number;
  organizer_grants: number;
  is_organizer: boolean;
  has_avatar: boolean;
};

/** Слово, которое человек набирает руками; сервер сверяет его так же. */
export const DELETE_CONFIRMATION_WORD = "удалить";

export function deleteConfirmationMatches(value: string): boolean {
  return value.trim().toLocaleLowerCase("ru-RU") === DELETE_CONFIRMATION_WORD;
}

const REQUEST_TIMEOUT_MS = 30_000;

const NO_ANSWER_MESSAGE = "Сервер не ответил — проверьте соединение и попробуйте ещё раз";

// 401 здесь значит «сессии больше нет»: вышли на другой вкладке или профиль
// уже удалён. Отличить одно от другого по самому ответу нельзя — это делает
// getDeletionStatus по id попытки, а текст годится для обоих случаев.
const SESSION_ENDED_MESSAGE = "Сессия закончилась. Войдите снова, если профиль ещё нужно удалить";

function errorMessage(status: number, detail: string | null): string {
  if (status === 401) {
    return SESSION_ENDED_MESSAGE;
  }
  // Свои отказы сервер пишет по-русски; английский detail — служебный
  // (FastAPI, проверка сессии: «Not authenticated») и человеку ничего не скажет.
  if (detail && /[а-яё]/i.test(detail)) {
    return detail;
  }
  return `Не удалось выполнить запрос (${status})`;
}

async function accountFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  // Таймер живёт до конца чтения тела, а не до заголовков: оборвавшееся
  // после заголовков соединение иначе оставило бы окно крутиться без срока.
  const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await readResponse<T>(path, init, controller.signal);
  } finally {
    window.clearTimeout(timer);
  }
}

async function readResponse<T>(path: string, init: RequestInit | undefined, signal: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      credentials: "include",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      signal,
      ...init,
    });
  } catch {
    throw new ApiError(NO_ANSWER_MESSAGE, 0);
  }
  if (!response.ok) {
    let detail: string | null = null;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") {
        detail = data.detail;
      }
    } catch {
      // тело не JSON — оставляем сообщение по умолчанию
    }
    throw new ApiError(errorMessage(response.status, detail), response.status);
  }
  try {
    return (await response.json()) as T;
  } catch {
    // Заголовки дошли, тело оборвалось. Статус 0 — «исход неясен»: для
    // удаления это значит «спроси исход по id попытки» (DeleteAccountSection).
    throw new ApiError(NO_ANSWER_MESSAGE, 0);
  }
}

export function getAccountDeletionPreview(): Promise<AccountDeletionPreview> {
  return accountFetch<AccountDeletionPreview>("/account/deletion-preview");
}

/**
 * Случайный id попытки удаления. Сервер запоминает его после коммита, и
 * браузер, не получивший ответа, спрашивает исход по нему же
 * (getDeletionStatus). Один на страницу: повтор после обрыва — та же история.
 */
export function makeDeletionAttemptId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function deleteAccount(confirmation: string, attemptId: string): Promise<{ message: string }> {
  return accountFetch<{ message: string }>("/account/delete", {
    method: "POST",
    body: JSON.stringify({ confirmation, attempt_id: attemptId }),
  });
}

/** Удалила ли профиль попытка с этим id (вход не нужен — сессии уже нет). */
export function getDeletionStatus(attemptId: string): Promise<{ deleted: boolean }> {
  return accountFetch<{ deleted: boolean }>(`/account/deletion-status?attempt=${encodeURIComponent(attemptId)}`);
}
