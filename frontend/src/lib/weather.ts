// Погода на стартах — общие типы и форматирование для протокола, пробежек,
// локации и обзора. Значения и подпись (label/icon/summary) приходят с бэкенда
// (start_weather_service), здесь — только вывод.

export type WeatherBrief = {
  date: string;
  start_time_local: string | null;
  temperature_c: number | null;
  apparent_temperature_c: number | null;
  humidity_pct: number | null;
  weather_code: number | null;
  label: string;
  icon: string;
  wind_speed_ms: number | null;
  wind_gusts_ms: number | null;
  /** Осадки в окне «час до старта — два часа после», мм. */
  precipitation_run_mm: number | null;
  precipitation_before_mm: number | null;
  snowfall_cm: number | null;
  snow_depth_cm: number | null;
  day_temperature_min_c: number | null;
  day_temperature_max_c: number | null;
  day_precipitation_mm: number | null;
  sunrise_local: string | null;
  rain_kind: "dry" | "drizzle" | "rain" | "downpour";
  is_rain: boolean;
  is_snow_cover: boolean;
  /** Предварительная строка из прогнозной модели (субботний вечер), архив заменит. */
  is_preliminary: boolean;
  summary: string;
};

export type WeatherStartRef = {
  platform_code: string;
  event_number: number | null;
  finishers: number | null;
};

export type WeatherRecord = { weather: WeatherBrief; start: WeatherStartRef | null };

/** «−27°» / «12°» — целые градусы, настоящий минус. */
export function formatTemp(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  const rounded = Math.round(value);
  return rounded < 0 ? `−${Math.abs(rounded)}°` : `${rounded}°`;
}

/** Короткая подпись для таблиц: значок + градусы («🌧️ 12°»). */
export function weatherShort(weather: WeatherBrief | null | undefined): string {
  if (!weather) {
    return "—";
  }
  return `${weather.icon} ${formatTemp(weather.temperature_c)}`.trim();
}

/** Подсказка к короткой подписи: полная строка + пометка о предварительности. */
export function weatherTitle(weather: WeatherBrief | null | undefined): string | undefined {
  if (!weather) {
    return undefined;
  }
  return weather.is_preliminary
    ? `${weather.summary} · предварительно, архив уточнит в понедельник`
    : weather.summary;
}

export const RAIN_KIND_LABELS: Record<WeatherBrief["rain_kind"], string> = {
  dry: "без дождя",
  drizzle: "возможно, моросило",
  rain: "дождь",
  downpour: "ливень",
};

/** Класс тона для температуры: мороз — синий, жара — красный, остальное нейтрально. */
export function temperatureTone(value: number | null | undefined): "frost" | "cold" | "mild" | "warm" | "hot" | "none" {
  if (value == null) {
    return "none";
  }
  if (value <= -10) {
    return "frost";
  }
  if (value < 0) {
    return "cold";
  }
  if (value < 20) {
    return "mild";
  }
  if (value < 28) {
    return "warm";
  }
  return "hot";
}
