/**
 * Раскладка аналитики дашборда: вместо сплошной стены из ~30 равнозначных
 * плиток — четыре тематические группы. У каждой группы своя витрина
 * (ровно одна строка плиток), свёрнутый хвост за кнопкой «Ещё N» и свои
 * панели-графики, живущие внутри группы, а не в общем подвале страницы.
 *
 * Порядок ключей в `cards` — это приоритет: витрину набираем сверху вниз
 * теми плитками, которые реально есть у участника. Нет побед или рекордов
 * — место занимает следующая по списку, и строка всё равно остаётся
 * полной. Так витрина не «вылезает» во вторую строку у одних и не зияет
 * дырами у других (решение Дмитрия, 28.08.2026).
 *
 * Карточка, чьего ключа нет ни в одной группе (например, новая метрика,
 * добавленная только в билдер карточек), не пропадает: DashboardAnalytics
 * дорисовывает такие в отдельной сетке после групп.
 */
export type DashboardAnalyticsGroup = {
  key: string;
  title: string;
  /** Ключи карточек по убыванию важности: первые заполняют витрину. */
  cards: readonly string[];
  /** Панели-графики группы (ключи из панелей DashboardAnalytics). */
  panels: readonly string[];
};

/** Сколько плиток помещается в одну строку витрины на десктопе. */
export const GROUP_HEADLINE_LIMIT = 4;

export const DASHBOARD_ANALYTICS_GROUPS: readonly DashboardAnalyticsGroup[] = [
  {
    key: "regularity",
    title: "Регулярность",
    cards: [
      "saturday_streak",
      "saturday_consistency",
      "first_run_date",
      "runs_year",
      "runs_12m",
      "total_distance",
      "days_since_first_run",
      "next_milestone",
      "top_location",
    ],
    panels: ["activity_calendar", "activity_chart"],
  },
  {
    key: "speed",
    title: "Скорость и рекорды",
    // Лучший результат открывает витрину, следом трофеи — победы и рекорды
    // (решение Дмитрия, 28.08.2026).
    cards: [
      "best_finish",
      "wins",
      "location_records",
      "age_group_records",
      "avg_finish",
      "avg_pace",
      "pr_count",
      "avg_position",
      "avg_gender_position",
    ],
    panels: ["pace_trend", "finish_distribution", "platform_metrics"],
  },
  {
    key: "tourism",
    title: "Беговой туризм",
    cards: [
      "unique_run_locations",
      "unique_run_cities",
      "new_locations_12m",
      "home_distance",
      "unique_run_regions",
    ],
    panels: [],
  },
  {
    key: "volunteering",
    title: "Волонтёрство",
    // Порядок витрины задан Дмитрием (28.08.2026): всего — за год —
    // локации — индекс. «Всего» здесь потому, что до группы человек
    // долистывает, когда шапка кабинета уже ушла за экран.
    cards: [
      "volunteering_total",
      "volunteering_year",
      "unique_volunteer_locations",
      "volunteering_index",
      "volunteering_12m",
      "unique_roles",
      "unique_volunteer_regions",
      "unique_volunteer_cities",
      "top_role",
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
