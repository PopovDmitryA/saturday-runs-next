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

// Единица зачёта туристических рейтингов: считать площадки, города или регионы.
// Колонки видны все три всегда, фильтр решает, что попадает в «Всего» и по чему
// строится место. Набор кнопок приходит с бэкенда (count_by_options).
export type CountBy = "locations" | "cities" | "regions";
export const COUNT_BY_LABELS: Record<CountBy, string> = {
  locations: "Локаций",
  cities: "Городов",
  regions: "Регионов",
};

// Площадка недели в колонке «Последняя неделя»: площадка и дата визита — тем же
// видом, что «Последняя победа» в победных рейтингах.
export type WeekLocation = {
  name: string;
  slug: string | null;
  date: string | null;
};

// Фильтр «смотреть по одной системе» есть у каждого рейтинга: объединённый
// зачёт — норма, но результат внутри одной системы всегда можно посмотреть
// отдельно. Набор кнопок зависит от рейтинга и зачёта (в гендерном нет
// parkrun, в волонтёрском туризме — тоже), поэтому его присылает бэкенд в
// platform_options, а не повторяет фронт.
export type PlatformFilter = "all" | "five_verst" | "s95" | "runpark" | "parkrun";

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
  // Только у туристических рейтингов: площадки / города / регионы. Одно из трёх
  // совпадает с total — какое, решает фильтр count_by.
  locations_total?: number | null;
  cities_total?: number | null;
  regions_total?: number | null;
  // Колонка «Последняя неделя»: где участник был за окно дельты.
  week_locations?: WeekLocation[];
};

export type LeaderboardResponse = {
  metric: string;
  gender?: LeaderboardGender;
  min_visits?: number;
  platform?: PlatformFilter;
  count_by?: CountBy;
  // Кнопки фильтра «единица зачёта»; пусто — у рейтинга такого фильтра нет.
  count_by_options?: CountBy[];
  has_week_locations?: boolean;
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
  count_by?: CountBy;
  locations_total?: number | null;
  cities_total?: number | null;
  regions_total?: number | null;
  week_locations?: WeekLocation[];
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
  countBy: CountBy = "locations",
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
  if (countBy !== "locations") {
    params.set("count_by", countBy);
  }
  return leaderboardsFetch<LeaderboardResponse>(`/leaderboards/${metric}?${params}`);
}

export function getMyLeaderboardRow(
  metric: LeaderboardMetric,
  gender: LeaderboardGender = "all",
  minVisits = 1,
  platform: PlatformFilter = "all",
  countBy: CountBy = "locations",
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
  if (countBy !== "locations") {
    params.set("count_by", countBy);
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
