// API раздела «Рейтинги» (сквозные лидерборды). Отдельный модуль, чтобы не
// трогать горячий lib/api.ts (его правят параллельные worktree-фичи).

export type LeaderboardMetric =
  | "runs"
  | "volunteering"
  | "volunteer_roles"
  | "locations"
  | "volunteer_locations"
  | "wins"
  | "win_locations";

export type LeaderboardGender = "all" | "male" | "female";

// Разрез М/Ж есть только у победных рейтингов (parkrun в него не идёт).
export const GENDERED_METRICS: LeaderboardMetric[] = ["wins", "win_locations"];

// Фильтр «локация засчитывается от N визитов» — у туристических рейтингов
// (перенос фильтра из старого дашборда Grafana): бегового и волонтёрского.
export const MIN_VISITS_METRICS: LeaderboardMetric[] = ["locations", "volunteer_locations"];
export const MIN_VISITS_OPTIONS = [1, 2, 3, 4, 5] as const;

// Фильтр «смотреть по одной системе» есть у каждого рейтинга: объединённый
// зачёт — норма, но результат внутри одной системы всегда можно посмотреть
// отдельно. Набор кнопок зависит от рейтинга и зачёта (в гендерном нет
// parkrun, в волонтёрском туризме — тоже), поэтому его присылает бэкенд в
// platform_options, а не повторяет фронт.
export type PlatformFilter = "all" | "five_verst" | "s95" | "runpark" | "parkrun";

// Фильтр «какие роли считать» — только у рейтингов по числу выходов; в
// «Мультиволонтёре» роли и есть сама метрика, фильтровать их там нечего.
export const ROLE_FILTER_METRICS: LeaderboardMetric[] = ["volunteering", "volunteer_locations"];

export type VolunteerRolePreset = "all" | "on_site" | "on_site_no_run" | "remote" | "custom";

// Роль справочника: ярлык плюс признаки, по которым собираются пресеты.
// Разметку держит бэкенд (app/volunteer_role_taxonomy.py) — фронт её не копирует.
export type VolunteerRoleItem = {
  key: string;
  label: string;
  on_site: boolean;
  runnable: boolean;
};

export type VolunteerRoleCatalog = {
  presets: string[];
  metrics: string[];
  roles: VolunteerRoleItem[];
};

export function getVolunteerRoleCatalog() {
  return leaderboardsFetch<VolunteerRoleCatalog>("/leaderboards/volunteer-roles/catalog");
}

/** Ключи ролей пресета. null — «все роли», параметр в запрос не уходит. */
export function presetRoleKeys(
  preset: VolunteerRolePreset,
  roles: VolunteerRoleItem[],
): string[] | null {
  if (preset === "on_site") {
    return roles.filter((role) => role.on_site).map((role) => role.key);
  }
  if (preset === "on_site_no_run") {
    return roles.filter((role) => role.on_site && !role.runnable).map((role) => role.key);
  }
  if (preset === "remote") {
    return roles.filter((role) => !role.on_site).map((role) => role.key);
  }
  return null;
}

// Одна освоенная роль в детализации мультиволонтёра: сколько волонтёрств и в
// каких системах. Приходит только у метрики volunteer_roles.
export type VolunteerRoleDetail = {
  role: string;
  total: number;
  platforms: Record<string, number>;
};

export type LeaderboardCell = {
  value: number;
  delta: number;
};

export type LeaderboardRow = {
  rank: number;
  rank_delta: number;
  display_name: string | null;
  site_serial_id: number | null;
  platforms: Record<string, LeaderboardCell>;
  total: number;
  total_delta: number;
  // Только у метрики wins: «топ-локация побед» — локация с максимумом побед.
  home_location?: string | null;
  home_location_wins?: number | null;
  // Только у победных рейтингов: глобальный рекорд участника и последняя победа
  // (у win_locations — последняя НОВАЯ локация с победой, дата — первой победы там).
  best_time_sec?: number | null;
  best_time_display?: string | null;
  last_win_location?: string | null;
  last_win_location_slug?: string | null;
  last_win_date?: string | null;
  // Только у мультиволонтёра: любимая роль (чаще всего выходил) и детализация
  // «роль × система × волонтёрств» — она раскрывается по клику на строке.
  top_role?: string | null;
  top_role_count?: number | null;
  role_details?: VolunteerRoleDetail[];
};

export type LeaderboardResponse = {
  metric: string;
  gender?: LeaderboardGender;
  min_visits?: number;
  platform?: PlatformFilter;
  title: string;
  description: string;
  unit: string;
  platform_columns: string[];
  // Кнопки фильтра «по системе» для этого рейтинга и зачёта: "all" + коды систем.
  platform_options?: PlatformFilter[];
  rows: LeaderboardRow[];
  threshold: number;
  median: number;
  entrants: number;
  latest_event_date: string | null;
  week_start: string | null;
  built_at: string | null;
};

export type MyLeaderboardRow = {
  metric: string;
  min_visits?: number;
  platform?: PlatformFilter;
  display_name: string | null;
  site_serial_id: number;
  platforms: Record<string, LeaderboardCell>;
  total: number;
  total_delta: number;
  rank: number | null;
  rank_delta: number | null;
  included: boolean;
  threshold: number;
  // true только в гендерном зачёте, когда пол участника (по истории финишей)
  // определённо не совпадает с выбранным — порог показывать не нужно.
  gender_mismatch?: boolean;
  home_location?: string | null;
  home_location_wins?: number | null;
  best_time_sec?: number | null;
  best_time_display?: string | null;
  last_win_location?: string | null;
  last_win_location_slug?: string | null;
  last_win_date?: string | null;
  top_role?: string | null;
  top_role_count?: number | null;
  role_details?: VolunteerRoleDetail[];
};

async function leaderboardsFetch<T>(path: string): Promise<T> {
  const response = await fetch(`/api${path}`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (response.status === 401) {
    throw new Error("unauthorized");
  }
  if (!response.ok) {
    throw new Error(`Не удалось загрузить данные (${response.status})`);
  }
  return (await response.json()) as T;
}

export function getLeaderboard(
  metric: LeaderboardMetric,
  limit = 1000,
  gender: LeaderboardGender = "all",
  minVisits = 1,
  platform: PlatformFilter = "all",
  roles: string[] | null = null,
) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (gender !== "all") {
    params.set("gender", gender);
  }
  if (minVisits > 1) {
    params.set("min_visits", String(minVisits));
  }
  if (platform !== "all") {
    params.set("platform", platform);
  }
  for (const role of roles ?? []) {
    params.append("roles", role);
  }
  return leaderboardsFetch<LeaderboardResponse>(`/leaderboards/${metric}?${params}`);
}

export function getMyLeaderboardRow(
  metric: LeaderboardMetric,
  gender: LeaderboardGender = "all",
  minVisits = 1,
  platform: PlatformFilter = "all",
  roles: string[] | null = null,
) {
  const params = new URLSearchParams();
  if (gender !== "all") {
    params.set("gender", gender);
  }
  if (minVisits > 1) {
    params.set("min_visits", String(minVisits));
  }
  if (platform !== "all") {
    params.set("platform", platform);
  }
  for (const role of roles ?? []) {
    params.append("roles", role);
  }
  const query = params.toString();
  return leaderboardsFetch<MyLeaderboardRow>(
    `/leaderboards/${metric}/me${query ? `?${query}` : ""}`,
  );
}

export const PLATFORM_LABELS: Record<string, string> = {
  five_verst: "5 вёрст",
  s95: "С95",
  runpark: "RunPark",
  parkrun: "parkrun",
};
