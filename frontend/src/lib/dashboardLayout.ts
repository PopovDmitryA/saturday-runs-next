/**
 * Раскладка аналитики дашборда: вместо сплошной стены из ~30 равнозначных
 * плиток — четыре тематические группы. У каждой группы есть «витрина»
 * (headline — всегда видимые главные цифры), свёрнутый хвост (rest — за
 * кнопкой «Ещё N») и свои панели-графики, живущие внутри группы, а не в
 * общем подвале страницы.
 *
 * Карточка, чьего ключа нет ни в одной группе (например, новая метрика,
 * добавленная только в билдер карточек), не пропадает: DashboardAnalytics
 * дорисовывает такие в отдельной сетке после групп.
 */
export type DashboardAnalyticsGroup = {
  key: string;
  title: string;
  /** Всегда видимые карточки-«витрина» группы. */
  headline: readonly string[];
  /** Карточки, скрытые за кнопкой «Ещё N». */
  rest: readonly string[];
  /** Панели-графики группы (ключи из панелей DashboardAnalytics). */
  panels: readonly string[];
};

export const DASHBOARD_ANALYTICS_GROUPS: readonly DashboardAnalyticsGroup[] = [
  {
    key: "regularity",
    title: "Регулярность",
    headline: ["saturday_streak", "saturday_consistency", "runs_year", "next_milestone"],
    // Суммарный километраж — про объём набеганного, а не про поездки:
    // место ему здесь, а не в туризме (решение Дмитрия, 09.08.2026).
    rest: ["runs_12m", "total_distance", "days_since_first_run"],
    panels: ["activity_calendar", "activity_chart"],
  },
  {
    key: "speed",
    title: "Скорость и рекорды",
    // Среднее время финиша — базовая цифра «как я обычно бегу», её прячать
    // за «Ещё N» нельзя: решение Дмитрия (09.08.2026).
    headline: ["best_finish", "avg_finish", "avg_pace", "pr_count", "wins"],
    rest: [
      "avg_position",
      "avg_gender_position",
      "location_records",
      "age_group_records",
    ],
    panels: ["pace_trend", "finish_distribution", "platform_metrics"],
  },
  {
    key: "tourism",
    title: "Беговой туризм",
    headline: ["unique_run_locations", "unique_run_cities", "new_locations_12m", "home_distance"],
    rest: ["unique_run_regions"],
    panels: [],
  },
  {
    key: "volunteering",
    title: "Волонтёрство",
    headline: [
      "volunteering_year",
      "volunteering_index",
      "unique_roles",
      "unique_volunteer_locations",
    ],
    rest: [
      "volunteering_12m",
      "unique_volunteer_regions",
      "unique_volunteer_cities",
      "top_role",
      "top_location",
    ],
    panels: [],
  },
];

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

/**
 * Профиль участника → группа аналитики: выбор профилей в модалке фокуса
 * решает, какие группы видны на дашборде. Пустой выбор или null — все.
 */
export const FOCUS_PROFILE_TO_GROUP: Record<string, string> = {
  regular: "regularity",
  racer: "speed",
  tourist: "tourism",
  volunteer: "volunteering",
};
