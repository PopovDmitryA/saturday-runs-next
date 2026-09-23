// Рейтинги трасс и паспорт трассы локации. Пока фича закрыта, эти ручки
// отвечают 404 всем, кроме админа (см. tracks_public_enabled на бэкенде).

export type CourseRatingMetric = "elevation" | "straightness";

export const COURSE_METRIC_LABELS: Record<CourseRatingMetric, string> = {
  elevation: "Перепад высот",
  straightness: "Прямолинейность",
};

export type CourseRatingItem = {
  position: number | null;
  location_slug: string;
  location_name: string;
  city: string | null;
  region: string | null;
  platform_code: string;
  last_event_date: string | null;
  // false — треков для этой локации пока не хватает, строка показывается серой.
  has_data: boolean;
  value: number | null;
  tracks_count: number;
  unique_user_count: number;
  distance_m: number | null;
  elevation_span_m: number | null;
  elevation_gain_m: number | null;
  turn_sum_deg: number | null;
  longest_straight_m: number | null;
  lap_count: number | null;
};

export type CourseRatingResponse = {
  metric: string;
  items: CourseRatingItem[];
  with_data_count: number;
  total_count: number;
};

export type CourseProfile = {
  course_version: number;
  is_current: boolean;
  tracks_count: number;
  unique_user_count: number;
  distance_m: number | null;
  distance_min_m: number | null;
  distance_max_m: number | null;
  elevation_gain_m: number | null;
  elevation_span_m: number | null;
  turn_sum_deg: number | null;
  u_turn_count: number | null;
  longest_straight_m: number | null;
  lap_count: number | null;
  uphill_share: number | null;
  downhill_share: number | null;
  climb_length_m: number | null;
  climb_rise_m: number | null;
  climb_grade_percent: number | null;
  elevation_profile: [number, number][];
  // Линия трассы для карты: [[широта, долгота], ...]
  geometry: [number, number][];
  first_track_at: string | null;
  last_track_at: string | null;
};

export type LocationCourseResponse = {
  location_slug: string;
  location_name: string;
  has_data: boolean;
  current: CourseProfile | null;
  // Прежние версии трассы: её меняли, эти цифры — история.
  history: CourseProfile[];
};

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`/api${path}`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    // 404 здесь штатный: пока фича закрыта, ручка не существует для всех,
    // кроме админа. Отличаем это от настоящей поломки — иначе непонятно,
    // что делать.
    if (response.status === 404 || response.status === 401) {
      throw new Error("Раздел пока закрыт: нужен вход под администратором.");
    }
    throw new Error(`Не удалось загрузить данные (${response.status})`);
  }
  return (await response.json()) as T;
}

export function getCourseRating(metric: CourseRatingMetric) {
  return request<CourseRatingResponse>(`/ratings/courses/${metric}`);
}

export function getLocationCourse(slug: string) {
  return request<LocationCourseResponse>(`/locations/${encodeURIComponent(slug)}/course`);
}
