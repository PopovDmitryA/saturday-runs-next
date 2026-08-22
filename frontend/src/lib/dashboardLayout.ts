/** Display order for square and wide analytics cards on the dashboard. */
export const DASHBOARD_ANALYTICS_CARD_ORDER = [
  "unique_run_locations",
  "unique_run_regions",
  "unique_run_cities",
  "wins",
  "avg_position",
  "avg_gender_position",
  "avg_finish",
  "best_finish",
  "avg_pace",
  "runs_12m",
  "runs_year",
  "new_locations_12m",
  "home_distance",
  "total_distance",
  "next_milestone",
  "pr_count",
  "location_records",
  "age_group_records",
  "last_global_pr_date",
  "days_since_first_run",
  "saturday_streak",
  "saturday_consistency",
  "volunteering_12m",
  "volunteering_year",
  "unique_volunteer_locations",
  "unique_volunteer_regions",
  "unique_volunteer_cities",
  "volunteering_index",
  "unique_roles",
  "top_role",
  "top_location",
] as const;

export const DASHBOARD_ANALYTICS_PANEL_ORDER = [
  "platform_metrics",
  "activity_chart",
  "activity_calendar",
  "pace_trend",
  "finish_distribution",
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
