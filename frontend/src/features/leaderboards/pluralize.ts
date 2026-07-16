import type { LeaderboardMetric } from "./leaderboardsApi";

// [один, два-четыре, пять-...] — стандартные русские формы склонения по числительному.
const UNIT_FORMS: Record<LeaderboardMetric, [string, string, string]> = {
  runs: ["пробежка", "пробежки", "пробежек"],
  volunteering: ["волонтёрство", "волонтёрства", "волонтёрств"],
  locations: ["локация", "локации", "локаций"],
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

export function unitLabel(metric: LeaderboardMetric, count: number): string {
  return pluralForm(count, UNIT_FORMS[metric]);
}
