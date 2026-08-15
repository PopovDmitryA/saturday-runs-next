// API раздела «Рейтинги» (сквозные лидерборды). Отдельный модуль, чтобы не
// трогать горячий lib/api.ts (его правят параллельные worktree-фичи).

export type LeaderboardMetric =
  | "runs"
  | "volunteering"
  | "volunteer_roles"
  | "locations"
  | "volunteer_locations"
  | "openings"
  | "wins"
  | "win_locations"
  | "home_distance";

// Мужского зачёта нет — только абсолют и женский. Пол мы знаем лишь по
// возрастной категории протокола, а у финишёров без аккаунта её нет: такой
// человек выпадает из строя мужчин, и следующий за ним получает «первое место
// среди мужчин», стоя в протоколе вторым (см. LEADERBOARD_GENDERS на бэкенде).
export type LeaderboardGender = "all" | "female";

/**
 * Единица измерения значения рейтинга — мелким шрифтом сразу за числом.
 * Живёт здесь, а не на странице рейтинга: то же число показывает хаб, и без
 * общей константы «км» там терялось (репорт Дмитрия 04.08.2026).
 */
export const METRIC_VALUE_UNIT: Partial<Record<LeaderboardMetric, string>> = {
  home_distance: "км",
};

/**
 * Рейтинги, ещё закрытые от публики: карточки нет в хабе, страница отдаёт 404,
 * API отвечает «неизвестный рейтинг» всем, кроме админа. Сейчас таких нет —
 * «Открытия» открыты всем 15.08.2026 после ручной разметки С95.
 *
 * Зеркало ADMIN_ONLY_METRICS на бэкенде — закрывать/открывать рейтинг надо в
 * обоих местах (плюс indexable в pageMeta.ts и seo_service.py).
 */
export const ADMIN_ONLY_METRICS: LeaderboardMetric[] = [];

// Женский зачёт есть только у победных рейтингов (parkrun в него не идёт).
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

// Подпись «Всего» в туристических рейтингах называет единицу зачёта: рядом
// стоят столбцы «Городов» и «Регионов», и голое «Всего» среди них не читалось.
export const COUNT_BY_TOTAL_LABELS: Record<CountBy, string> = {
  locations: "Всего локаций",
  cities: "Всего городов",
  regions: "Всего регионов",
};

// Колонка «Последняя неделя»: последний старт участника за окно дельты —
// площадка и дата, тем же видом, что «Последняя победа» в победных рейтингах.
// Всегда ровно одна площадка: если стартов было несколько, бэкенд отдаёт
// самый поздний.
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
  home_location_slug?: string | null;
  home_location_wins?: number | null;
  // Только у home_distance: "ambiguous" — автовыбор дома шаткий,
  // "manual_off_top" — человек выбрал руками площадку вне своей тройки.
  home_location_note?: "ambiguous" | "manual_off_top" | null;
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
  week_location?: WeekLocation | null;
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
  /** Фильтр «только очевидный дом»: есть ли он у рейтинга и включён ли. */
  has_home_filter?: boolean;
  hide_ambiguous_home?: boolean;
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
  /** Через сколько часов после built_at таблица пересчитается (TTL снапшота). */
  refresh_hours?: number;
};

export type MyLeaderboardRow = {
  metric: string;
  min_visits?: number;
  platform?: PlatformFilter;
  count_by?: CountBy;
  locations_total?: number | null;
  cities_total?: number | null;
  regions_total?: number | null;
  week_location?: WeekLocation | null;
  display_name: string | null;
  site_serial_id: number;
  platforms: Record<string, LeaderboardCell>;
  total: number;
  total_delta: number;
  rank: number | null;
  // Место среди всех с ненулевой метрикой — есть и у тех, кто порог рейтинга
  // ещё не прошёл (в отличие от rank).
  rank_overall?: number | null;
  rank_delta: number | null;
  included: boolean;
  threshold: number;
  // true только в гендерном зачёте, когда пол участника (по истории финишей)
  // определённо не совпадает с выбранным — порог показывать не нужно.
  gender_mismatch?: boolean;
  home_location?: string | null;
  home_location_slug?: string | null;
  home_location_wins?: number | null;
  // Только у home_distance: "ambiguous" — автовыбор дома шаткий,
  // "manual_off_top" — человек выбрал руками площадку вне своей тройки.
  home_location_note?: "ambiguous" | "manual_off_top" | null;
  /**
   * Только у home_distance: когда участник менял домашнюю локацию в настройках.
   * Своя строка считается вживую, а таблица приходит из снапшота — если отметка
   * свежее built_at таблицы, в её строках ещё километры от прежнего дома.
   */
  home_location_changed_at?: string | null;
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
  roles: string[] | null = null,
  hideAmbiguousHome = false,
) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (hideAmbiguousHome) {
    params.set("hide_ambiguous_home", "true");
  }
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
  countBy: CountBy = "locations",
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
  if (countBy !== "locations") {
    params.set("count_by", countBy);
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
