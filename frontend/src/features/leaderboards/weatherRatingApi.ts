// API рейтинга «Погода»: моржи, суровые локации, температура против результата.
// Свой модуль, как у регионов: строки здесь трёх разных видов, с типами
// лидербордов людей у них ничего общего нет.
import type { WeatherBrief } from "../../lib/weather";

export type WalrusRow = {
  place: number;
  name: string;
  handle: string | null;
  platform_code: string;
  /** Стартов при −20° и ниже. */
  count: number;
  coldest_c: number | null;
  coldest_label: string;
  coldest_date: string | null;
  coldest_location: string | null;
};

export type SeasonLocationRow = {
  place: number;
  name: string;
  slug: string | null;
  platform_code: string;
  region: string | null;
  starts: number;
  median_c: number;
  min_c: number;
  max_c: number;
};

export type TemperatureBucket = {
  from_c: number;
  to_c: number;
  label: string;
  finishes: number;
  starts: number;
  avg_finish_sec: number | null;
  avg_finishers: number | null;
};

export type WeatherExtreme = {
  location_name: string;
  location_slug: string | null;
  platform_code: string;
  finishers: number | null;
  event_number: number | null;
  weather: WeatherBrief;
};

export type WeatherRatingResponse = {
  walruses: { by_count: WalrusRow[]; coldest: WalrusRow[]; participants: number; threshold_c: number };
  locations: { coldest_winter: SeasonLocationRow[]; hottest_summer: SeasonLocationRow[] };
  temperature: TemperatureBucket[];
  extremes: Record<string, WeatherExtreme | null>;
};

export async function getWeatherRating(): Promise<WeatherRatingResponse> {
  const response = await fetch("/api/weather-rating", {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`Не удалось загрузить рейтинг (${response.status})`);
  }
  return (await response.json()) as WeatherRatingResponse;
}
