/** Display order for square and wide analytics cards on the dashboard. */
export const DASHBOARD_ANALYTICS_CARD_ORDER = [
  "unique_run_locations",
  "avg_position",
  "avg_finish",
  "best_finish",
  "avg_pace",
  "runs_12m",
  "runs_year",
  "new_locations_12m",
  "total_distance",
  "next_milestone",
  "pr_count",
  "last_pr_date",
  "days_since_first_run",
  "saturday_streak",
  "saturday_consistency",
  "volunteering_12m",
  "volunteering_year",
  "unique_volunteer_locations",
  "volunteering_index",
  "unique_roles",
  "top_role",
  "top_location",
  "activity_period",
] as const;

export const DASHBOARD_ANALYTICS_PANEL_ORDER = [
  "platform_metrics",
  "activity_chart",
  "pace_trend",
] as const;

export function sortByLayoutOrder<T extends { key: string }>(
  items: T[],
  order: readonly string[],
): T[] {
  const rank = new Map(order.map((key, index) => [key, index]));
  return [...items].sort(
    (a, b) =>
      (rank.get(a.key) ?? Number.MAX_SAFE_INTEGER) - (rank.get(b.key) ?? Number.MAX_SAFE_INTEGER),
  );
}
