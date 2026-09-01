// API журналов посещаемости: режим «Журнал» рейтингов и журнал локации.
// Отдельный модуль, чтобы не трогать горячие lib/api.ts и leaderboardsApi.ts
// (их правят параллельные worktree-фичи).

export type JournalMetric = "runs" | "volunteering" | "locations";

// Метрики рейтингов, у которых есть режим «Журнал» (зеркало JOURNAL_METRICS
// на бэкенде).
export const JOURNAL_METRICS: JournalMetric[] = ["runs", "volunteering", "locations"];

export type JournalItem = {
  date: string;
  location: string | null;
  slug: string | null;
  platform: string;
  // Только у журнала волонтёрств: роль (канонический ярлык).
  role?: string | null;
  // Только у журнала туризма: первый визит на площадку за всю историю.
  new?: boolean | null;
};

export type JournalRow = {
  row_key: string;
  rank: number | null;
  display_name: string | null;
  site_serial_id: number | null;
  // «Всего» рейтинга за всю историю — как в таблице рядом.
  total: number | null;
  // Счёт выбранного года (у туризма — новые площадки года).
  year_total: number;
  // Закрытый профиль: счёт года есть, отметки по датам скрыты.
  private: boolean;
  items: JournalItem[];
};

export type AttendanceJournal = {
  metric: string;
  year: number;
  years: number[];
  platform: string;
  offset: number;
  limit: number;
  total_rows: number;
  latest_event_date: string | null;
  rows: JournalRow[];
  me: JournalRow | null;
};

export type LocationAttendanceItem = {
  date: string;
  run: boolean;
  roles: string[];
};

export type LocationAttendanceRow = {
  name: string | null;
  handle: string | null;
  private: boolean;
  // Дней активности в году: день с пробежкой и волонтёрством — один день.
  year_total: number;
  runs_total: number;
  volunteering_total: number;
  items: LocationAttendanceItem[];
};

export type LocationAttendanceKind = "all" | "runners" | "volunteers";

export type LocationAttendance = {
  slug: string;
  name: string;
  year: number;
  years: number[];
  kind: LocationAttendanceKind;
  offset: number;
  limit: number;
  total_rows: number;
  columns: { date: string; platforms: string[] }[];
  date_totals: Record<string, { runners: number; volunteers: number; people?: number }>;
  rows: LocationAttendanceRow[];
  me: LocationAttendanceRow | null;
};

async function journalFetch<T>(path: string): Promise<T> {
  const response = await fetch(`/api${path}`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`Не удалось загрузить журнал (${response.status})`);
  }
  return (await response.json()) as T;
}

export function getAttendanceJournal(
  metric: JournalMetric,
  options: { year?: number | null; platform?: string; offset?: number } = {},
): Promise<AttendanceJournal> {
  const params = new URLSearchParams();
  if (options.year != null) {
    params.set("year", String(options.year));
  }
  if (options.platform && options.platform !== "all") {
    params.set("platform", options.platform);
  }
  if (options.offset) {
    params.set("offset", String(options.offset));
  }
  const query = params.toString();
  return journalFetch<AttendanceJournal>(
    `/leaderboards/${metric}/journal${query ? `?${query}` : ""}`,
  );
}

export function getLocationAttendance(
  slug: string,
  options: { year?: number | null; kind?: LocationAttendanceKind; offset?: number } = {},
): Promise<LocationAttendance> {
  const params = new URLSearchParams();
  if (options.year != null) {
    params.set("year", String(options.year));
  }
  if (options.kind && options.kind !== "all") {
    params.set("kind", options.kind);
  }
  if (options.offset) {
    params.set("offset", String(options.offset));
  }
  const query = params.toString();
  return journalFetch<LocationAttendance>(
    `/locations/page/${encodeURIComponent(slug)}/attendance${query ? `?${query}` : ""}`,
  );
}
