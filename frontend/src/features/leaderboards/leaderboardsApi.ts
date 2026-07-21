// API раздела «Рейтинги» (сквозные лидерборды). Отдельный модуль, чтобы не
// трогать горячий lib/api.ts (его правят параллельные worktree-фичи).

export type LeaderboardMetric =
  | "runs"
  | "volunteering"
  | "locations"
  | "wins"
  | "win_locations";

export type LeaderboardGender = "all" | "male" | "female";

// Разрез М/Ж есть только у победных рейтингов (parkrun в него не идёт).
export const GENDERED_METRICS: LeaderboardMetric[] = ["wins", "win_locations"];

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
};

export type LeaderboardResponse = {
  metric: string;
  gender?: LeaderboardGender;
  title: string;
  description: string;
  unit: string;
  platform_columns: string[];
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
  display_name: string | null;
  site_serial_id: number;
  platforms: Record<string, LeaderboardCell>;
  total: number;
  total_delta: number;
  rank: number | null;
  rank_delta: number | null;
  included: boolean;
  threshold: number;
  home_location?: string | null;
  home_location_wins?: number | null;
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
) {
  const genderParam = gender === "all" ? "" : `&gender=${gender}`;
  return leaderboardsFetch<LeaderboardResponse>(
    `/leaderboards/${metric}?limit=${limit}${genderParam}`,
  );
}

export function getMyLeaderboardRow(
  metric: LeaderboardMetric,
  gender: LeaderboardGender = "all",
) {
  const genderParam = gender === "all" ? "" : `?gender=${gender}`;
  return leaderboardsFetch<MyLeaderboardRow>(`/leaderboards/${metric}/me${genderParam}`);
}

export const PLATFORM_LABELS: Record<string, string> = {
  five_verst: "5 вёрст",
  s95: "С95",
  runpark: "RunPark",
  parkrun: "parkrun",
};
