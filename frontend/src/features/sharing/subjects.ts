// Адаптеры сюжетов: API-типы → нормализованный ShareCardData.
//
// Это единственное место, где движок знает о данных сайта. Новый сюжет
// (встречи, Wrapped-слайд, …) = ещё одна функция здесь, рендер не меняется.

import type {
  DashboardStats,
  LocationPage,
  LocationPersonalStats,
  MyHistoryMilestone,
  OnThisDayRun,
  RunItem,
  User,
  VolunteeringItem,
} from "../../lib/api";
import {
  formatDate,
  formatDuration,
  platformCodeLabel,
  pluralFormRu,
  pluralizeRu,
} from "../../lib/format";
import { milestoneAccentLabel } from "../history/milestoneShare";
import type { LeaderboardResponse, MyLeaderboardRow } from "../leaderboards/leaderboardsApi";
import type { ShareCardData, ShareMetric, ShareSubject } from "./types";

function displayName(user: User | null | undefined): string {
  const custom = user?.display_name?.trim();
  if (custom) {
    return custom;
  }
  if (user?.telegram_username) {
    return `@${user.telegram_username.replace(/^@/, "")}`;
  }
  return "Участник";
}

/** «01:23:45» → «23:45»: часы у 5-километровых времён почти всегда нули. */
function stripLeadingHours(display: string | null | undefined): string | null {
  if (!display) {
    return null;
  }
  return display.replace(/^00:/, "");
}

function pushMetric(metrics: ShareMetric[], id: string, value: string | null | undefined, label: string): void {
  if (value != null && value !== "" && value !== "—") {
    metrics.push({ id, value, label });
  }
}

// ── Пробежка (последняя, любая, «В этот день») ─────────────────────────────

export function runSubject(run: RunItem, user: User | null, options?: { yearsAgo?: number }): ShareSubject {
  // Порядок = приоритет: первые попадают на карточку, остальные доступны
  // в «Настроить». Чем больше кандидатов, тем интереснее собирать свой постер.
  const metrics: ShareMetric[] = [];
  pushMetric(metrics, "position", run.position != null ? String(run.position) : null, "место");
  pushMetric(metrics, "pace", run.pace_display, "темп /км");
  pushMetric(
    metrics,
    "gender_position",
    run.gender_position != null ? String(run.gender_position) : null,
    "место по полу",
  );
  pushMetric(metrics, "age_category", run.age_category, "возрастная группа");
  pushMetric(metrics, "location", run.location_name, "локация");
  pushMetric(metrics, "event_number", run.event_number != null ? `#${run.event_number}` : null, "номер старта");
  pushMetric(metrics, "platform", platformCodeLabel(run.platform_code), "система");
  pushMetric(metrics, "date", formatDate(run.event_date), "дата");
  pushMetric(metrics, "city", run.location_city, "город");
  pushMetric(metrics, "country", run.location_country, "страна");
  pushMetric(metrics, "club", run.club_name, "клуб");
  if (run.is_first_run_at_location) {
    pushMetric(metrics, "first_here", "впервые", "на этой локации");
  }
  if (run.is_first_run) {
    pushMetric(metrics, "first_run", "дебют", "первая пробежка");
  }
  for (const label of run.achievement_labels ?? []) {
    pushMetric(metrics, `achievement_${label}`, label, "достижение");
  }

  const yearsAgo = options?.yearsAgo;
  const time = stripLeadingHours(run.finish_time_display);
  const data: ShareCardData = {
    audience: "runner",
    title: displayName(user),
    subtitle: `${run.location_name} · ${formatDate(run.event_date)}`,
    plate: yearsAgo != null && yearsAgo > 0 ? `В этот день · ${pluralizeRu(yearsAgo, ["год", "года", "лет"])} назад` : undefined,
    hero: time ? { value: time, caption: `${platformCodeLabel(run.platform_code)} · 5 км` } : undefined,
    chip: run.is_global_pr
      ? "Личный рекорд — все системы"
      : run.is_pr
        ? "Личный рекорд"
        : undefined,
    metrics,
  };
  return {
    kind: "run",
    data,
    fileName: `run5k-run-${run.event_date}`,
    defaultFormat: "story",
  };
}

export function onThisDaySubject(run: OnThisDayRun, user: User | null): ShareSubject {
  const item: RunItem = {
    platform_code: run.platform_code,
    event_date: run.event_date,
    event_number: null,
    location_name: run.location_name,
    location_city: run.location_city,
    location_country: null,
    position: run.position,
    gender_position: null,
    finish_time_display: run.finish_time_display,
    finish_time_sec: run.finish_time_sec,
    pace_display: null,
    pace_sec_per_km: null,
    age_category: null,
    is_pr: run.is_pr,
    is_global_pr: false,
    is_location_pr: false,
    is_crosslinked: false,
    is_first_run: false,
    is_first_run_at_location: false,
    club_name: null,
    achievement_labels: [],
    status: null,
    is_test_event: false,
    event_url: run.event_url,
  };
  const subject = runSubject(item, user, { yearsAgo: run.years_ago });
  return { ...subject, fileName: `run5k-memory-${run.event_date}` };
}

// ── Веха «Моей истории» ─────────────────────────────────────────────────────

const NUMBER_CAPTIONS: Partial<Record<string, (n: number) => string>> = {
  run_club: (n) => pluralFormRu(n, ["пробежка", "пробежки", "пробежек"]),
  location_club: (n) => `${pluralFormRu(n, ["пробежка", "пробежки", "пробежек"])} на локации`,
  volunteer_first: () => "волонтёрство",
  volunteer_club: (n) => pluralFormRu(n, ["волонтёрство", "волонтёрства", "волонтёрств"]),
  streak_record: (n) => `${pluralFormRu(n, ["суббота", "субботы", "суббот"])} подряд`,
};

export function milestoneSubject(milestone: MyHistoryMilestone, user: User | null): ShareSubject {
  const accent = milestoneAccentLabel(milestone);
  const metrics: ShareMetric[] = [];
  pushMetric(metrics, "location", milestone.location_name, platformCodeLabel(milestone.platform_code));
  pushMetric(metrics, "time", stripLeadingHours(milestone.finish_time_display), "время");
  pushMetric(
    metrics,
    "position",
    milestone.position != null ? String(milestone.position) : null,
    "место",
  );
  pushMetric(
    metrics,
    "gender_position",
    milestone.gender_position != null ? String(milestone.gender_position) : null,
    "место по полу",
  );
  pushMetric(metrics, "pace", milestone.pace_display, "темп /км");
  pushMetric(metrics, "age_group", milestone.age_group, "возрастная группа");
  pushMetric(metrics, "role", milestone.role, "роль волонтёра");
  pushMetric(metrics, "date", formatDate(milestone.event_date), "дата");
  pushMetric(metrics, "platform", platformCodeLabel(milestone.platform_code), "система");
  pushMetric(metrics, "city", milestone.location_city, "город");
  pushMetric(metrics, "region", milestone.region, "регион");
  pushMetric(metrics, "country", milestone.country, "страна");

  let hero: ShareCardData["hero"];
  const captionFor = NUMBER_CAPTIONS[milestone.kind];
  if (milestone.number != null && captionFor) {
    hero = { value: String(milestone.number), caption: captionFor(milestone.number) };
  } else if (milestone.number != null && /клуб|регион|город/i.test(accent)) {
    hero = { value: String(milestone.number), caption: accent.toLowerCase() };
  } else if (milestone.finish_time_display) {
    const isRecord = milestone.kind.includes("pr") || milestone.kind.includes("record");
    hero = {
      value: stripLeadingHours(milestone.finish_time_display) ?? "",
      caption: isRecord ? "новое лучшее время" : "финишное время",
    };
    // Время уже в герое — из плиток его убираем.
    const timeIndex = metrics.findIndex((metric) => metric.id === "time");
    if (timeIndex >= 0) {
      metrics.splice(timeIndex, 1);
    }
  }

  const delta = milestone.delta_sec;
  const data: ShareCardData = {
    audience: "runner",
    title: displayName(user),
    subtitle: formatDate(milestone.event_date),
    plate: accent,
    hero,
    chip: delta != null && delta < 0 ? `−${Math.abs(delta)} сек к личному рекорду` : undefined,
    metrics,
    fact: milestone.role ?? undefined,
  };
  return {
    kind: "milestone",
    data,
    fileName: `run5k-milestone-${milestone.event_date}`,
    defaultFormat: "story",
  };
}

// ── Сводка (сезон / вся история) ────────────────────────────────────────────

function lastSaturdays(count: number): string[] {
  const days: string[] = [];
  const cursor = new Date();
  // Откат к ближайшей прошедшей субботе (сб = 6).
  const offset = (cursor.getDay() + 1) % 7;
  cursor.setDate(cursor.getDate() - offset);
  for (let i = 0; i < count; i += 1) {
    days.unshift(cursor.toISOString().slice(0, 10));
    cursor.setDate(cursor.getDate() - 7);
  }
  return days;
}

export function summarySubject(
  stats: DashboardStats,
  user: User | null,
  period: "all" | "year",
  runs: RunItem[],
): ShareSubject {
  const analytics = stats.analytics;
  const year = new Date().getFullYear();
  const yearRuns = runs.filter((run) => run.event_date >= `${year}-01-01`);
  const metrics: ShareMetric[] = [];

  if (period === "year") {
    pushMetric(metrics, "runs", String(yearRuns.length), pluralFormRu(yearRuns.length, ["пробежка", "пробежки", "пробежек"]));
    pushMetric(
      metrics,
      "volunteering",
      analytics.volunteering_current_year != null ? String(analytics.volunteering_current_year) : null,
      "волонтёрств",
    );
    const uniqueLocations = new Set(yearRuns.map((run) => `${run.platform_code}:${run.location_name}`)).size;
    pushMetric(metrics, "locations", uniqueLocations > 0 ? String(uniqueLocations) : null, "локаций");
    pushMetric(metrics, "distance", String(yearRuns.length * 5), "километров");
    const prCount = yearRuns.filter((run) => run.is_pr).length;
    pushMetric(metrics, "prs", prCount > 0 ? String(prCount) : null, "личных рекордов");
  } else {
    pushMetric(metrics, "runs", String(stats.total_runs ?? 0), "пробежек");
    pushMetric(
      metrics,
      "volunteering",
      stats.total_volunteering ? String(stats.total_volunteering) : null,
      "волонтёрств",
    );
    pushMetric(
      metrics,
      "locations",
      analytics.unique_locations ? String(analytics.unique_locations) : null,
      "локаций",
    );
    pushMetric(
      metrics,
      "distance",
      analytics.total_distance_km ? String(Math.round(analytics.total_distance_km)) : null,
      "километров",
    );
    pushMetric(
      metrics,
      "best_time",
      analytics.best_finish_time_sec != null ? stripLeadingHours(formatDuration(analytics.best_finish_time_sec)) : null,
      "лучшее время",
    );
  }
  pushMetric(
    metrics,
    "streak",
    analytics.saturday_streak ? String(analytics.saturday_streak) : null,
    "суббот подряд",
  );
  const consistency = analytics.saturday_consistency_pct;
  pushMetric(
    metrics,
    "consistency",
    consistency != null ? `${Math.round(consistency)}%` : null,
    "суббот с активностью",
  );

  // Дальше — общий «хвост» кандидатов: на карточку по умолчанию не попадают,
  // но их можно выбрать в «Настроить».
  pushMetric(
    metrics,
    "wins",
    analytics.wins_count ? String(analytics.wins_count) : null,
    analytics.wins_scope === "female" ? "побед среди женщин" : "первых мест",
  );
  pushMetric(
    metrics,
    "prs_total",
    analytics.pr_count ? String(analytics.pr_count) : null,
    "личных рекордов",
  );
  pushMetric(
    metrics,
    "regions",
    analytics.unique_run_regions ? String(analytics.unique_run_regions) : null,
    "регионов",
  );
  pushMetric(
    metrics,
    "cities",
    analytics.unique_run_cities ? String(analytics.unique_run_cities) : null,
    "городов",
  );
  pushMetric(
    metrics,
    "avg_time",
    analytics.avg_finish_time_sec != null
      ? stripLeadingHours(formatDuration(Math.round(analytics.avg_finish_time_sec)))
      : null,
    "среднее время",
  );
  pushMetric(
    metrics,
    "avg_pace",
    analytics.avg_pace_sec_per_km != null
      ? `${Math.floor(analytics.avg_pace_sec_per_km / 60)}:${String(Math.round(analytics.avg_pace_sec_per_km % 60)).padStart(2, "0")}`
      : null,
    "средний темп /км",
  );
  pushMetric(
    metrics,
    "avg_position",
    analytics.avg_position != null ? String(Math.round(analytics.avg_position)) : null,
    "среднее место",
  );
  pushMetric(
    metrics,
    "streak_max",
    analytics.saturday_streak_max ? String(analytics.saturday_streak_max) : null,
    "рекорд серии суббот",
  );
  pushMetric(
    metrics,
    "years",
    analytics.days_since_first_run != null
      ? String(Math.max(1, Math.round(analytics.days_since_first_run / 365)))
      : null,
    pluralFormRu(
      analytics.days_since_first_run != null
        ? Math.max(1, Math.round(analytics.days_since_first_run / 365))
        : 0,
      ["год в движении", "года в движении", "лет в движении"],
    ),
  );
  pushMetric(metrics, "top_location", analytics.top_location?.name, "любимая локация");
  pushMetric(metrics, "top_role", analytics.top_volunteer_role?.role, "частая роль");
  pushMetric(
    metrics,
    "volunteer_roles",
    analytics.unique_volunteer_roles ? String(analytics.unique_volunteer_roles) : null,
    "ролей волонтёра",
  );
  pushMetric(
    metrics,
    "club",
    analytics.run_clubs_earned?.length
      ? String(analytics.run_clubs_earned[analytics.run_clubs_earned.length - 1])
      : null,
    "клуб пробежек",
  );

  // Мини-календарь: последние 24 субботы, отмечены дни с активностью.
  const activeDays = new Set((analytics.activity_calendar ?? []).map((day) => day.date));
  const heat = lastSaturdays(24).map((day) => activeDays.has(day));

  const data: ShareCardData = {
    audience: "runner",
    title: displayName(user),
    subtitle: period === "year" ? `Итоги ${year}` : "Вся история",
    plate: period === "year" ? `СЕЗОН ${year}` : "МОЯ СТАТИСТИКА",
    metrics,
    heat: heat.some(Boolean) ? heat : undefined,
    fact: analytics.top_location ? `Любимая локация — ${analytics.top_location.name}` : undefined,
  };
  return {
    kind: "summary",
    data,
    fileName: `run5k-summary-${period === "year" ? year : "all"}`,
    defaultFormat: "story",
  };
}

// ── Локация: последний старт и визитка ─────────────────────────────────────

function locationTitle(page: LocationPage): string {
  return page.city ? `${page.name} · ${page.city}` : page.name;
}

export function locationEventSubject(page: LocationPage): ShareSubject | null {
  const last = page.stats.last_event;
  if (!last) {
    return null;
  }
  const stats = page.stats;
  const metrics: ShareMetric[] = [];
  pushMetric(metrics, "volunteers", last.volunteers ? String(last.volunteers) : null, "волонтёров");
  pushMetric(metrics, "avg_time", stripLeadingHours(last.avg_time_display), "среднее время");
  pushMetric(metrics, "best_male", stripLeadingHours(last.best_male_time_display), "лучшее · М");
  pushMetric(metrics, "best_female", stripLeadingHours(last.best_female_time_display), "лучшее · Ж");
  pushMetric(metrics, "debutants", last.debutants ? String(last.debutants) : null, "новичков");
  pushMetric(
    metrics,
    "first_at_location",
    last.first_at_location ? String(last.first_at_location) : null,
    "впервые здесь",
  );
  pushMetric(metrics, "prs", last.prs ? String(last.prs) : null, "личных рекордов");
  pushMetric(metrics, "date", formatDate(last.event_date), "дата старта");
  pushMetric(metrics, "platform", platformCodeLabel(last.platform_code), "система");
  // Контекст площадки: с чем сравнивать цифры этой субботы.
  pushMetric(
    metrics,
    "avg_finishers",
    stats.avg_finishers ? String(Math.round(stats.avg_finishers)) : null,
    "в среднем на старте",
  );
  pushMetric(
    metrics,
    "attendance_record",
    stats.attendance_record ? String(stats.attendance_record.finishers) : null,
    "рекорд посещаемости",
  );
  pushMetric(metrics, "location_name", page.name, "локация");

  const attendance = page.stats.attendance_record;
  const isAttendanceRecord = attendance != null && attendance.event_date === last.event_date;

  const data: ShareCardData = {
    audience: "location",
    title: locationTitle(page),
    subtitle: `${formatDate(last.event_date)} · ${platformCodeLabel(last.platform_code)}`,
    plate: "ПОСЛЕДНИЙ СТАРТ",
    hero: { value: String(last.finishers), caption: "финишёров" },
    chip: isAttendanceRecord ? "Рекорд посещаемости!" : undefined,
    metrics,
  };
  return {
    kind: "location_event",
    data,
    fileName: `run5k-${page.slug}-last-event`,
    defaultFormat: "wide",
  };
}

export function locationCardSubject(page: LocationPage): ShareSubject {
  const stats = page.stats;
  const metrics: ShareMetric[] = [];
  pushMetric(metrics, "events", stats.events_count ? String(stats.events_count) : null, "стартов");
  pushMetric(
    metrics,
    "participants",
    stats.unique_participants ? String(stats.unique_participants) : null,
    "участников",
  );
  pushMetric(
    metrics,
    "finishers",
    stats.finishers_total ? String(stats.finishers_total) : null,
    "финишей",
  );
  const male = stats.course_records?.male;
  const female = stats.course_records?.female;
  pushMetric(metrics, "record_male", stripLeadingHours(male?.finish_time_display), "рекорд · М");
  pushMetric(metrics, "record_female", stripLeadingHours(female?.finish_time_display), "рекорд · Ж");
  pushMetric(
    metrics,
    "attendance",
    stats.attendance_record ? String(stats.attendance_record.finishers) : null,
    "рекорд посещаемости",
  );
  pushMetric(
    metrics,
    "volunteers",
    stats.unique_volunteers ? String(stats.unique_volunteers) : null,
    "волонтёров",
  );
  pushMetric(
    metrics,
    "avg_finishers",
    stats.avg_finishers ? String(Math.round(stats.avg_finishers)) : null,
    "в среднем на старте",
  );
  pushMetric(
    metrics,
    "median_time",
    stripLeadingHours(stats.median_finish_time_display),
    "медианное время",
  );
  pushMetric(metrics, "avg_time", stripLeadingHours(stats.avg_finish_time_display), "среднее время");

  const firstYear = stats.first_event_date?.slice(0, 4);
  const timeline = page.platforms.map((platform) => {
    const first = platform.first_event_date?.slice(0, 4);
    const last = platform.last_event_date?.slice(0, 4);
    return {
      label: platformCodeLabel(platform.platform_code),
      period: platform.is_active ? `с ${first ?? "?"} · сейчас` : `${first ?? "?"} — ${last ?? "?"}`,
      current: platform.is_active === true,
    };
  });

  const data: ShareCardData = {
    audience: "location",
    title: locationTitle(page),
    subtitle: firstYear ? `с ${firstYear} года` : undefined,
    plate: "ЛОКАЦИЯ В ЦИФРАХ",
    timeline: timeline.length > 1 ? timeline : undefined,
    metrics,
  };
  return {
    kind: "location_card",
    data,
    fileName: `run5k-${page.slug}`,
    defaultFormat: "wide",
  };
}

// ── Позиция в рейтинге ──────────────────────────────────────────────────────

export function ratingSubject(
  board: LeaderboardResponse,
  me: MyLeaderboardRow,
  user: User | null,
): ShareSubject | null {
  if (me.rank == null) {
    return null;
  }
  const metrics: ShareMetric[] = [];
  pushMetric(metrics, "total", String(me.total), board.unit);
  // Разбивка по системам — каждая отдельной плиткой (на выбор в «Настроить»).
  const platformEntries = Object.entries(me.platforms ?? {}).filter(([, cell]) => cell && cell.value > 0);
  for (const [code, cell] of platformEntries.sort((a, b) => b[1].value - a[1].value)) {
    pushMetric(metrics, `platform_${code}`, String(cell.value), platformCodeLabel(code));
  }
  pushMetric(
    metrics,
    "best_time",
    stripLeadingHours(me.best_time_display),
    "лучшее время",
  );
  pushMetric(
    metrics,
    "cities_total",
    me.cities_total != null ? String(me.cities_total) : null,
    "городов",
  );
  pushMetric(
    metrics,
    "regions_total",
    me.regions_total != null ? String(me.regions_total) : null,
    "регионов",
  );
  pushMetric(metrics, "top_role", me.top_role, "любимая роль");
  pushMetric(
    metrics,
    "entrants",
    board.entrants ? String(board.entrants) : null,
    "всего в рейтинге",
  );
  pushMetric(
    metrics,
    "threshold",
    board.threshold ? String(board.threshold) : null,
    "порог попадания",
  );

  // Название рейтинга — крупной плашкой (мелкий подзаголовок не бросался в
  // глаза); «из N участников» — только в подписи героя, без плитки-дубля.
  const data: ShareCardData = {
    audience: "runner",
    title: displayName(user),
    plate: board.title,
    hero: {
      value: `№${me.rank}`,
      caption: board.entrants ? `из ${board.entrants} участников` : undefined,
    },
    metrics,
  };
  return {
    kind: "rating",
    data,
    fileName: `run5k-rating-${board.metric}`,
    defaultFormat: "story",
  };
}

// ── Волонтёрство (строка таблицы) ───────────────────────────────────────────

export function volunteeringSubject(item: VolunteeringItem, user: User | null): ShareSubject {
  const metrics: ShareMetric[] = [];
  pushMetric(metrics, "location", item.location_name, "локация");
  pushMetric(metrics, "date", formatDate(item.event_date), "дата");
  pushMetric(metrics, "platform", platformCodeLabel(item.platform_code), "система");
  pushMetric(metrics, "city", item.location_city, "город");
  pushMetric(metrics, "country", item.location_country, "страна");
  pushMetric(
    metrics,
    "event_number",
    item.event_number != null ? `#${item.event_number}` : null,
    "номер старта",
  );
  pushMetric(
    metrics,
    "parkrun_credits",
    item.parkrun_total_credits != null ? String(item.parkrun_total_credits) : null,
    "всего волонтёрств parkrun",
  );

  const data: ShareCardData = {
    audience: "runner",
    title: displayName(user),
    subtitle: `${item.location_name} · ${formatDate(item.event_date)}`,
    plate: "ВОЛОНТЁРСТВО",
    hero: item.role
      ? { value: item.role, caption: "роль на старте" }
      : undefined,
    metrics,
  };
  return {
    kind: "volunteering",
    data,
    fileName: `run5k-volunteering-${item.event_date}`,
    defaultFormat: "story",
  };
}

// ── «Я на этой локации» (личная статистика на площадке) ────────────────────

export function locationMeSubject(stats: LocationPersonalStats, user: User | null): ShareSubject | null {
  if (stats.runs_count <= 0 && stats.volunteering_count <= 0) {
    return null;
  }
  const metrics: ShareMetric[] = [];
  // Локация — первой плиткой: мелкий подзаголовок над карточкой не читался,
  // а именно площадка тут главный смысл.
  pushMetric(metrics, "location", stats.name, "локация");
  pushMetric(
    metrics,
    "runs",
    stats.runs_count > 0 ? String(stats.runs_count) : null,
    "пробежек здесь",
  );
  pushMetric(metrics, "best_time", stripLeadingHours(stats.best_time_display), "лучшее время");
  pushMetric(metrics, "avg_time", stripLeadingHours(stats.avg_time_display), "среднее время");
  pushMetric(
    metrics,
    "volunteering",
    stats.volunteering_count > 0 ? String(stats.volunteering_count) : null,
    "волонтёрств",
  );
  if (stats.rank_by_runs_gender != null) {
    const scope = stats.gender === "female" ? "среди женщин" : stats.gender === "male" ? "среди мужчин" : "в топе";
    pushMetric(metrics, "rank", `№${stats.rank_by_runs_gender}`, `${scope} площадки`);
  }
  pushMetric(
    metrics,
    "best_time_date",
    stats.best_time_date ? formatDate(stats.best_time_date) : null,
    "дата рекорда здесь",
  );
  pushMetric(
    metrics,
    "first_run",
    stats.first_run_date ? formatDate(stats.first_run_date) : null,
    "первый старт здесь",
  );
  pushMetric(
    metrics,
    "last_run",
    stats.last_run_date ? formatDate(stats.last_run_date) : null,
    "последний старт",
  );
  pushMetric(
    metrics,
    "share_of_all",
    stats.total_runs > 0 && stats.runs_count > 0
      ? `${Math.round((stats.runs_count / stats.total_runs) * 100)}%`
      : null,
    "от всех моих стартов",
  );
  pushMetric(
    metrics,
    "home_distance",
    stats.home_distance?.distance_km != null
      ? `${Math.round(stats.home_distance.distance_km)} км`
      : null,
    "от домашней локации",
  );
  for (const group of stats.age_groups ?? []) {
    pushMetric(
      metrics,
      `age_group_${group.key}`,
      group.place != null ? `№${group.place}` : null,
      `в группе ${group.label}`,
    );
  }
  const firstYear = stats.first_run_date?.slice(0, 4);

  const data: ShareCardData = {
    audience: "runner",
    title: displayName(user),
    subtitle: firstYear ? `${stats.name} · с ${firstYear} года` : stats.name,
    plate: "Я НА ЭТОЙ ЛОКАЦИИ",
    hero:
      stats.runs_count > 0
        ? { value: String(stats.runs_count), caption: pluralFormRu(stats.runs_count, ["пробежка", "пробежки", "пробежек"]) }
        : undefined,
    // Число пробежек уже в герое — плитку-дубль убираем, локация остаётся.
    metrics: stats.runs_count > 0 ? metrics.filter((metric) => metric.id !== "runs") : metrics,
    fact:
      stats.total_runs > 0 && stats.runs_count > 0
        ? `${stats.runs_count} из ${stats.total_runs} моих стартов — здесь`
        : undefined,
  };
  return {
    kind: "location_me",
    data,
    fileName: `run5k-${stats.slug}-me`,
    defaultFormat: "story",
  };
}
