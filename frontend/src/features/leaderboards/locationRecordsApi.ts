// API рейтинга рекордов локаций. Отдельный модуль от leaderboardsApi: там
// строка рейтинга — участник, здесь — площадка, и общих типов у них нет.

export type LocationRecordsScope = "absolute" | "age_group";
export type LocationRecordsGender = "male" | "female";

// Фильтр «смотреть по одной системе» — только в абсолютном зачёте: возрастные
// категории есть в протоколе лишь у 5 вёрст.
export type LocationRecordsPlatform = "all" | "five_verst" | "s95" | "runpark" | "parkrun";

export const LOCATION_RECORDS_PLATFORM_LABELS: Record<string, string> = {
  all: "Все",
  five_verst: "5 вёрст",
  s95: "С95",
  runpark: "RunPark",
  parkrun: "parkrun",
};

export type LocationRecordRow = {
  place: number;
  slug: string;
  name: string;
  city: string | null;
  region: string | null;
  is_paused: boolean;
  is_cancelled: boolean;
  finish_time_sec: number;
  finish_time_display: string | null;
  runner_name: string | null;
  runner_handle: string | null;
  event_date: string | null;
  platform_code: string | null;
  platform_label: string | null;
  protocol_url: string | null;
};

export type LocationRecordsAgeGroup = {
  age_group: string;
  key: string;
  locations_count: number;
};

export type LocationRecordsResponse = {
  scope: LocationRecordsScope;
  gender: LocationRecordsGender;
  age_group: string | null;
  platform: LocationRecordsPlatform;
  rows: LocationRecordRow[];
  age_groups: LocationRecordsAgeGroup[];
  platforms: string[];
  viewer_age_group: string | null;
  viewer_gender: string | null;
};

export async function getLocationRecords(params: {
  scope: LocationRecordsScope;
  gender?: LocationRecordsGender | null;
  ageGroup?: string | null;
  platform?: LocationRecordsPlatform;
}): Promise<LocationRecordsResponse> {
  const query = new URLSearchParams({ scope: params.scope });
  if (params.gender) {
    query.set("gender", params.gender);
  }
  if (params.scope === "age_group" && params.ageGroup) {
    query.set("age_group", params.ageGroup);
  }
  if (params.scope === "absolute" && params.platform && params.platform !== "all") {
    query.set("platform", params.platform);
  }
  const response = await fetch(`/api/location-records?${query}`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`Не удалось загрузить рейтинг (${response.status})`);
  }
  return (await response.json()) as LocationRecordsResponse;
}
