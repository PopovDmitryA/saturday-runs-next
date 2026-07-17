export type PortalHero = {
  finishes_total: number;
  participants_total: number;
  locations_total: number;
  starts_total: number;
};

export type PortalPulse = {
  event_date: string;
  starts: number;
  finishes: number;
  newcomers: number;
  volunteers: number;
  personal_records: number;
};

export type PortalAttendanceRecord = {
  location_name: string;
  platform_code: string;
  event_date: string;
  finishers: number;
  previous_record: number;
  previous_record_date: string | null;
};

export type PortalCourseRecord = {
  location_name: string;
  platform_code: string;
  event_date: string;
  gender: "male" | "female";
  time_display: string;
  runner_name: string | null;
  previous_display: string | null;
  previous_record_date: string | null;
  delta_sec: number | null;
};

export type PortalWeekRecords = {
  week_start: string;
  week_end: string;
  attendance: PortalAttendanceRecord[];
  course: PortalCourseRecord[];
};

export type PortalTopSaturdayRow = {
  location_name: string;
  platform_code: string;
  finishers: number;
};

export type PortalYearPoint = { year: number; total: number; platforms: Record<string, number> };
export type PortalYearProjection = {
  year: number;
  weeks_elapsed: number;
  total: number;
  platforms: Record<string, number>;
};
export type PortalWeekPoint = { week_start: string; total: number; platforms: Record<string, number> };
export type PortalLocationsYearPoint = { year: number; platforms: Record<string, number> };
export type PortalLocationsWeekPoint = { week_start: string; platforms: Record<string, number> };

export type PortalBusiestDay = { event_date: string; finishers: number };
export type PortalBusiestDayPlatform = {
  platform_code: string;
  event_date: string;
  finishers: number;
};

export type PortalFastestRow = {
  platform_code: string;
  gender: "male" | "female";
  value_display: string;
  runner_name: string | null;
  location_name: string;
  event_date: string;
  delta_sec: number | null;
};

export type PortalAttendanceTopRow = {
  location_name: string;
  platform_code: string;
  event_date: string;
  finishers: number;
};

export type PortalSystemCard = {
  code: string;
  title: string;
  locations: number;
  finishes: number;
  first_year: number | null;
  last_year: number | null;
  is_active: boolean;
  avg_finish_display: string | null;
};

export type PortalGeoPoint = {
  latitude: number;
  longitude: number;
  starts: number;
  location_name: string;
  platform_code: string;
  region: string | null;
};

export type PortalGeo = {
  locations_total: number;
  regions_total: number;
  points: PortalGeoPoint[];
};

export type PortalRecordFact = {
  location_name: string;
  event_date: string;
  value_display: string;
  runner_name: string | null;
};

export type PortalFunFacts = {
  total_distance_km: number;
  total_time_sec: number;
  avg_finish_display: string | null;
  distance_delta_km: number | null;
  time_delta_sec: number | null;
  avg_delta_sec: number | null;
};

export type PortalGenderPlatform = { platform_code: string; male: number; female: number };
export type PortalGenderSplit = {
  total: { male: number; female: number };
  by_platform: PortalGenderPlatform[];
};

export type PortalHomeResponse = {
  generated_at: string;
  hero: PortalHero;
  hero_last_year: PortalHero | null;
  fastest: PortalFastestRow[];
  pulse: PortalPulse | null;
  week_records: PortalWeekRecords | null;
  top_saturday: PortalTopSaturdayRow[];
  chart_years: PortalYearPoint[];
  chart_year_projection: PortalYearProjection | null;
  chart_weeks: PortalWeekPoint[];
  locations_by_year: PortalLocationsYearPoint[];
  locations_by_week: PortalLocationsWeekPoint[];
  newcomers_by_year: PortalYearPoint[];
  newcomers_by_week: PortalWeekPoint[];
  newcomers_year_projection: PortalYearProjection | null;
  personal_records_by_year: PortalYearPoint[];
  personal_records_by_week: PortalWeekPoint[];
  personal_records_year_projection: PortalYearProjection | null;
  attendance_year: number | null;
  attendance_top_all: PortalAttendanceTopRow[];
  attendance_top_year: PortalAttendanceTopRow[];
  busiest_day_overall: PortalBusiestDay | null;
  busiest_day_by_platform: PortalBusiestDayPlatform[];
  systems: PortalSystemCard[];
  geo: PortalGeo;
  fun_facts: PortalFunFacts;
  gender_split: PortalGenderSplit | null;
};

export async function fetchPortalHome(): Promise<PortalHomeResponse> {
  const response = await fetch("/api/portal/home", { credentials: "same-origin" });
  if (!response.ok) {
    throw new Error(`Не удалось загрузить статистику (HTTP ${response.status})`);
  }
  return (await response.json()) as PortalHomeResponse;
}
