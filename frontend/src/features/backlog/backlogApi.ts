// API раздела «Бэклог». Отдельный модуль, а не lib/api.ts — тот файл
// одновременно правят другие worktree-фичи, паттерн взят из
// features/leaderboards/leaderboardsApi.ts.

export type BacklogCardType = "bug" | "feature";
export type BacklogCardStatus = "pending" | "in_progress" | "rejected" | "done";

// Фото карточки: ссылку собирает бэкенд (публичный S3 на проде,
// /api/media в локальной разработке).
export type BacklogPhoto = {
  id: string;
  url: string;
  width: number;
  height: number;
};

export const MAX_BACKLOG_PHOTOS = 3;

export type BacklogCard = {
  id: string;
  type: BacklogCardType;
  category: string;
  title: string;
  description: string;
  status: BacklogCardStatus;
  is_anonymous: boolean;
  author_display_name: string | null;
  author_handle: string | null;
  author_avatar_url: string | null;
  author_avatar_full_url: string | null;
  upvotes: number;
  downvotes: number;
  score: number;
  my_vote: number;
  comment_count: number;
  done_at: string | null;
  // Своя ли карточка для текущего зрителя — по нему рисуем «Редактировать».
  is_mine: boolean;
  photos: BacklogPhoto[];
  created_at: string;
  updated_at: string;
};

export type BacklogComment = {
  id: string;
  card_id: string;
  body: string;
  is_anonymous: boolean;
  author_display_name: string | null;
  author_handle: string | null;
  author_avatar_url: string | null;
  author_avatar_full_url: string | null;
  is_admin_author: boolean;
  // Считается на бэкенде: своя ли реплика для текущего зрителя (для чата).
  is_mine: boolean;
  created_at: string;
};

export type BacklogCardAdmin = Omit<BacklogCard, "my_vote"> & {
  comment_count: number;
};

export type BacklogVoteAdminItem = {
  value: number;
  author_display_name: string;
  author_handle: string;
  created_at: string;
};

export type BacklogVoteAdminList = {
  upvotes: BacklogVoteAdminItem[];
  downvotes: BacklogVoteAdminItem[];
};

export type BacklogCategory = { key: string; label: string };

// Дублирует backend/app/backlog_categories.py — единственный источник
// правды для валидации там же в коде, здесь просто список для <select>.
export const BACKLOG_CATEGORIES: BacklogCategory[] = [
  { key: "dashboard", label: "Личный кабинет" },
  { key: "runs", label: "Пробежки" },
  { key: "volunteering", label: "Волонтёрство" },
  { key: "achievements", label: "Достижения" },
  { key: "co_runners", label: "Встречи" },
  { key: "ratings", label: "Рейтинги" },
  { key: "maps", label: "Карта" },
  { key: "locations", label: "Локации" },
  { key: "blog", label: "Блог" },
  { key: "sync", label: "Синхронизация с системами" },
  { key: "account", label: "Профиль и настройки" },
  { key: "other", label: "Другое" },
];

export const BACKLOG_STATUS_LABELS: Record<BacklogCardStatus, string> = {
  pending: "На рассмотрении",
  in_progress: "В реализации",
  rejected: "Отклонено",
  done: "Реализовано",
};

export const BACKLOG_TYPE_LABELS: Record<BacklogCardType, string> = {
  bug: "Баг",
  feature: "Фича",
};

class BacklogApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function backlogFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!response.ok) {
    let detail = `Не удалось выполнить запрос (${response.status})`;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") {
        detail = data.detail;
      }
    } catch {
      // тело не JSON — оставляем сообщение по умолчанию
    }
    throw new BacklogApiError(detail, response.status);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export type BacklogCardFilters = {
  type?: BacklogCardType;
  category?: string;
  status?: BacklogCardStatus;
};

function buildQuery(filters: BacklogCardFilters): string {
  const params = new URLSearchParams();
  if (filters.type) params.set("type", filters.type);
  if (filters.category) params.set("category", filters.category);
  if (filters.status) params.set("status", filters.status);
  const query = params.toString();
  return query ? `?${query}` : "";
}

export function listBacklogCards(filters: BacklogCardFilters = {}) {
  return backlogFetch<{ items: BacklogCard[]; total: number }>(`/backlog/cards${buildQuery(filters)}`);
}

export function getBacklogCard(cardId: string) {
  return backlogFetch<BacklogCard>(`/backlog/cards/${cardId}`);
}

export function createBacklogCard(body: {
  type: BacklogCardType;
  category: string;
  title: string;
  description: string;
  is_anonymous: boolean;
}) {
  return backlogFetch<BacklogCard>("/backlog/cards", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// Поля, которые может править автор карточки. status в этот набор не входит —
// он меняется только админом (см. updateAdminBacklogCard).
export type BacklogCardEditableFields = {
  type?: BacklogCardType;
  category?: string;
  title?: string;
  description?: string;
  is_anonymous?: boolean;
};

export function updateBacklogCard(cardId: string, body: BacklogCardEditableFields) {
  return backlogFetch<BacklogCard>(`/backlog/cards/${cardId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function voteBacklogCard(cardId: string, value: -1 | 0 | 1) {
  return backlogFetch<BacklogCard>(`/backlog/cards/${cardId}/vote`, {
    method: "POST",
    body: JSON.stringify({ value }),
  });
}

// Загрузка идёт мимо backlogFetch: тот всегда ставит Content-Type
// application/json, а multipart нужен со своим boundary от браузера.
export async function uploadBacklogPhoto(cardId: string, file: File): Promise<BacklogPhoto> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`/api/backlog/cards/${cardId}/photos`, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (!response.ok) {
    let detail = `Не удалось загрузить фото (${response.status})`;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      // тело не JSON — оставляем сообщение по умолчанию
    }
    throw new BacklogApiError(detail, response.status);
  }
  return (await response.json()) as BacklogPhoto;
}

export function deleteBacklogPhoto(photoId: string) {
  return backlogFetch<void>(`/backlog/photos/${photoId}`, { method: "DELETE" });
}

export function listBacklogComments(cardId: string) {
  return backlogFetch<{ items: BacklogComment[] }>(`/backlog/cards/${cardId}/comments`);
}

export function createBacklogComment(cardId: string, body: { body: string; is_anonymous: boolean }) {
  return backlogFetch<BacklogComment>(`/backlog/cards/${cardId}/comments`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listAdminBacklogCards() {
  return backlogFetch<{ items: BacklogCardAdmin[]; total: number }>("/admin/backlog/cards");
}

export function updateAdminBacklogCardStatus(cardId: string, body: { status: BacklogCardStatus }) {
  return backlogFetch<BacklogCardAdmin>(`/admin/backlog/cards/${cardId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

// Админ правит любое поле карточки, включая статус.
export function updateAdminBacklogCard(
  cardId: string,
  body: BacklogCardEditableFields & { status?: BacklogCardStatus },
) {
  return backlogFetch<BacklogCardAdmin>(`/admin/backlog/cards/${cardId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteAdminBacklogCard(cardId: string) {
  return backlogFetch<{ message: string }>(`/admin/backlog/cards/${cardId}`, { method: "DELETE" });
}

export function deleteAdminBacklogComment(commentId: string) {
  return backlogFetch<{ message: string }>(`/admin/backlog/comments/${commentId}`, { method: "DELETE" });
}

export function listAdminBacklogVotes(cardId: string) {
  return backlogFetch<BacklogVoteAdminList>(`/admin/backlog/cards/${cardId}/votes`);
}
