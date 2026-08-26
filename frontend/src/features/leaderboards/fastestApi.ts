// API рейтинга быстрых (/ratings/fastest). Отдельный модуль от
// leaderboardsApi.ts: у этого рейтинга строка — не участник со счётчиками, а
// конкретный финиш, и общая форма LeaderboardRow ему не подходит.

export type FastestMode = "results" | "runners";

// Мужской зачёт здесь ЕСТЬ, в отличие от рейтингов побед: там его убрали из-за
// фантомных «первых мест среди мужчин» на стартах, где протокол не знает пол
// части финишёров, а тут места среди своего пола не считаются вовсе — строка
// без пола просто не попадает в гендерный срез.
export type FastestGender = "all" | "male" | "female";

export const FASTEST_MODE_LABELS: Record<FastestMode, string> = {
  results: "Финиши",
  runners: "Участники",
};

// Кнопки «Зачёт» — только М/Ж (решение Дмитрия 25.08.2026: «Абсолют» перегружал
// ряд фильтров). Сам зачёт «all» на бэкенде остался: по нему живёт карточка на
// хабе и открываются старые ссылки с gender=all.
export const FASTEST_GENDER_TABS: { value: FastestGender; label: string }[] = [
  { value: "male", label: "Мужчины" },
  { value: "female", label: "Женщины" },
];

/** Чем открывается страница, когда пол в адресе не задан. */
export const DEFAULT_FASTEST_GENDER: FastestGender = "male";

export const AGE_GROUP_ALL = "all";
// Единственная система с возрастными группами в протоколе.
export const AGE_GROUP_PLATFORM = "five_verst";
export const YEAR_ALL = "all";

export type FastestRow = {
  rank: number;
  row_key: string;
  display_name: string | null;
  site_serial_id: number | null;
  finish_time_sec: number;
  finish_time_display: string;
  platform: string;
  location_name: string | null;
  location_slug: string | null;
  event_date: string | null;
  protocol_url: string | null;
  age_group: string | null;
  age_category: string | null;
  gender: string | null;
};

export type FastestRatingResponse = {
  mode: FastestMode;
  platform: string;
  gender: FastestGender;
  age_group: string;
  year: string;
  limit: number;
  title: string;
  description: string;
  platform_options: string[];
  platform_labels: Record<string, string>;
  age_group_options: string[];
  // Единственная система с возрастными группами: только при ней даём выбрать ступень.
  age_group_platform: string;
  // Годы в разрезе систем (ключ "all" — объединение): переключение системы
  // подрезает список лет без похода на сервер.
  year_options_by_platform: Record<string, number[]>;
  rows: FastestRow[];
  built_at: string | null;
  refresh_hours: number;
};

export type MyFastestRow = {
  mode: FastestMode;
  platform: string;
  gender: FastestGender;
  age_group: string;
  year: string;
  row: FastestRow | null;
  rank: number | null;
  included: boolean;
};

export type FastestFilters = {
  mode: FastestMode;
  platform: string;
  gender: FastestGender;
  ageGroup: string;
  year: string;
};

function filterParams(filters: FastestFilters, limit?: number): string {
  const params = new URLSearchParams({ mode: filters.mode });
  if (limit != null) {
    params.set("limit", String(limit));
  }
  if (filters.platform !== "all") {
    params.set("platform", filters.platform);
  }
  if (filters.gender !== "all") {
    params.set("gender", filters.gender);
  }
  if (filters.ageGroup !== AGE_GROUP_ALL) {
    params.set("age_group", filters.ageGroup);
  }
  if (filters.year !== YEAR_ALL) {
    params.set("year", filters.year);
  }
  return params.toString();
}

async function fastestFetch<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, {
    credentials: "include",
    headers: { Accept: "application/json" },
    signal,
  });
  if (response.status === 401) {
    throw new Error("unauthorized");
  }
  if (!response.ok) {
    throw new Error(`Не удалось загрузить данные (${response.status})`);
  }
  return (await response.json()) as T;
}

/** limit укорачивает ответ, не меняя сам срез — для карточки на хабе. */
export function getFastestRating(filters: FastestFilters, limit?: number, signal?: AbortSignal) {
  return fastestFetch<FastestRatingResponse>(`/fastest?${filterParams(filters, limit)}`, signal);
}

export function getMyFastestRow(filters: FastestFilters, signal?: AbortSignal) {
  return fastestFetch<MyFastestRow>(`/fastest/me?${filterParams(filters)}`, signal);
}

/** Отменённый на лету запрос — не ошибка загрузки, а смена фильтра. */
export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}
