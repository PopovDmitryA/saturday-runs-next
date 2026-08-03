import type { CountBy, LeaderboardMetric } from "./leaderboardsApi";

// [один, два-четыре, пять-...] — стандартные русские формы склонения по числительному.
const UNIT_FORMS: Record<LeaderboardMetric, [string, string, string]> = {
  runs: ["пробежка", "пробежки", "пробежек"],
  volunteering: ["волонтёрство", "волонтёрства", "волонтёрств"],
  volunteer_roles: ["роль", "роли", "ролей"],
  locations: ["локация", "локации", "локаций"],
  volunteer_locations: ["локация", "локации", "локаций"],
  wins: ["первое место", "первых места", "первых мест"],
  win_locations: [
    "локация с первым местом",
    "локации с первым местом",
    "локаций с первым местом",
  ],
  home_distance: ["километр от дома", "километра от дома", "километров от дома"],
};

function pluralForm(count: number, forms: [string, string, string]): string {
  const abs = Math.abs(count) % 100;
  const last = abs % 10;
  if (abs > 10 && abs < 20) {
    return forms[2];
  }
  if (last === 1) {
    return forms[0];
  }
  if (last >= 2 && last <= 4) {
    return forms[1];
  }
  return forms[2];
}

// Единица зачёта туристических рейтингов подменяет единицу метрики: порог
// «появитесь после 4…» в режиме городов должен называть города, а не локации.
const COUNT_BY_FORMS: Record<Exclude<CountBy, "locations">, [string, string, string]> = {
  cities: ["город", "города", "городов"],
  regions: ["регион", "региона", "регионов"],
};

export function unitLabel(
  metric: LeaderboardMetric,
  count: number,
  countBy: CountBy = "locations",
): string {
  const forms =
    countBy === "locations" ? UNIT_FORMS[metric] : COUNT_BY_FORMS[countBy];
  return pluralForm(count, forms);
}
