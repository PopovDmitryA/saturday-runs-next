// Адаптеры сюжетов: API-типы → нормализованный ShareCardData.
//
// Это единственное место, где движок знает о данных сайта. Новый сюжет
// (встречи, Wrapped-слайд, …) = ещё одна функция здесь, рендер не меняется.

import type {
  DashboardStats,
  LocationPage,
  LocationProtocol,
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
  formatInt,
  formatNumber,
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

/**
 * Насколько цифра отличается от обычной для этой локации: «+81%», «−12%».
 * Мелкие отклонения не метрика, а шум, — до 5% отдаём null и плитки не будет.
 */
function deltaPercent(value: number | null | undefined, baseline: number | null | undefined): string | null {
  if (value == null || baseline == null || baseline <= 0) {
    return null;
  }
  const pct = Math.round(((value - baseline) / baseline) * 100);
  if (Math.abs(pct) < 5) {
    return null;
  }
  return `${pct > 0 ? "+" : "−"}${Math.abs(pct)}%`;
}

/** Разрыв во времени без знака: «2:23». Знак уезжает в подпись плитки. */
function formatGap(seconds: number): string {
  const abs = Math.round(Math.abs(seconds));
  return `${Math.floor(abs / 60)}:${String(abs % 60).padStart(2, "0")}`;
}

/** Сколько полных лет прошло с даты. Для «рекорд держится 5 лет». */
function fullYearsSince(date: string | null | undefined): number | null {
  if (!date) {
    return null;
  }
  const then = Date.parse(date);
  if (Number.isNaN(then)) {
    return null;
  }
  const years = Math.floor((Date.now() - then) / (365.2425 * 24 * 3600 * 1000));
  return years >= 0 ? years : null;
}

/**
 * Протоколы приходят с фамилией капсом («Алексей РЕУНКОВ»). На постере это
 * выглядит как крик, поэтому приводим к обычному виду, сохраняя дефисы.
 */
function humanizeName(name: string | null | undefined): string | null {
  const trimmed = name?.trim();
  if (!trimmed) {
    return null;
  }
  return trimmed
    .split(/\s+/)
    .map((word) =>
      word
        .split("-")
        .map((part) =>
          part.length > 1 && part === part.toUpperCase()
            ? part[0] + part.slice(1).toLowerCase()
            : part,
        )
        .join("-"),
    )
    .join(" ");
}

function pushMetric(
  metrics: ShareMetric[],
  id: string,
  value: string | null | undefined,
  label: string,
  options?: { keepLabelCase?: boolean },
): void {
  if (value != null && value !== "" && value !== "—") {
    metrics.push({ id, value, label, keepLabelCase: options?.keepLabelCase });
  }
}

// ── Пробежка (последняя, любая, «В этот день») ─────────────────────────────

export function runSubject(run: RunItem, user: User | null, options?: { yearsAgo?: number }): ShareSubject {
  // Порядок = приоритет: первые попадают на карточку, остальные доступны
  // в «Настроить». Чем больше кандидатов, тем интереснее собирать свой постер.
  const metrics: ShareMetric[] = [];
  pushMetric(metrics, "position", run.position != null ? formatInt(run.position) : null, "место");
  pushMetric(metrics, "pace", run.pace_display, "темп /км");
  pushMetric(
    metrics,
    "gender_position",
    run.gender_position != null ? formatInt(run.gender_position) : null,
    "место по полу",
  );
  pushMetric(metrics, "age_category", run.age_category, "возрастная группа");
  pushMetric(metrics, "event_number", run.event_number != null ? `#${run.event_number}` : null, "номер старта");
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
    milestone.position != null ? formatInt(milestone.position) : null,
    "место",
  );
  pushMetric(
    metrics,
    "gender_position",
    milestone.gender_position != null ? formatInt(milestone.gender_position) : null,
    "место по полу",
  );
  pushMetric(metrics, "pace", milestone.pace_display, "темп /км");
  pushMetric(metrics, "age_group", milestone.age_group, "возрастная группа");
  pushMetric(metrics, "role", milestone.role, "роль волонтёра");
  pushMetric(metrics, "date", formatDate(milestone.event_date), "дата");
  pushMetric(metrics, "city", milestone.location_city, "город");
  pushMetric(metrics, "region", milestone.region, "регион");
  pushMetric(metrics, "country", milestone.country, "страна");

  let hero: ShareCardData["hero"];
  const captionFor = NUMBER_CAPTIONS[milestone.kind];
  if (milestone.number != null && captionFor) {
    hero = { value: formatInt(milestone.number), caption: captionFor(milestone.number) };
  } else if (milestone.number != null && /клуб|регион|город/i.test(accent)) {
    hero = { value: formatInt(milestone.number), caption: accent.toLowerCase() };
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

/**
 * Карточка публичного профиля для OG-превью ссылки: тот же набор цифр, что и
 * «Сводка», но имя приходит строкой (серверный рендер работает без сессии,
 * объекта User там нет), формат — широкий, как разворачивают ссылки чаты.
 */
// Что показываем на превью ссылки и в каком порядке. Километры сознательно
// не берём: у пятикилометровых стартов это просто пробежки × 5, цифра ничего
// не добавляет — куда интереснее личный рекорд.
const PROFILE_CARD_METRICS = ["runs", "volunteering", "locations", "best_time"] as const;

export function profileCardSubject(stats: DashboardStats, name: string): ShareSubject {
  const summary = summarySubject(stats, null, "all", []);
  const byId = new Map(summary.data.metrics.map((metric) => [metric.id, metric]));
  const preferred = PROFILE_CARD_METRICS.map((id) => byId.get(id)).filter(
    (metric): metric is ShareMetric => Boolean(metric),
  );
  // Хвост (регионы, победы, серия…) остаётся кандидатом в «Настроить».
  const rest = summary.data.metrics.filter(
    (metric) => !PROFILE_CARD_METRICS.includes(metric.id as (typeof PROFILE_CARD_METRICS)[number]),
  );

  return {
    ...summary,
    kind: "summary",
    data: {
      ...summary.data,
      title: name,
      subtitle: "субботние пробежки",
      plate: "УЧАСТНИК",
      metrics: [...preferred, ...rest],
      // Мини-календарь в широком формате только съедает место под цифры.
      heat: undefined,
    },
    fileName: `run5k-profile-${name}`,
    defaultFormat: "wide",
  };
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
    pushMetric(metrics, "runs", formatInt(yearRuns.length), pluralFormRu(yearRuns.length, ["пробежка", "пробежки", "пробежек"]));
    pushMetric(
      metrics,
      "volunteering",
      analytics.volunteering_current_year != null ? formatInt(analytics.volunteering_current_year) : null,
      "волонтёрств",
    );
    const uniqueLocations = new Set(yearRuns.map((run) => `${run.platform_code}:${run.location_name}`)).size;
    pushMetric(metrics, "locations", uniqueLocations > 0 ? formatInt(uniqueLocations) : null, "локаций");
    pushMetric(metrics, "distance", formatInt(yearRuns.length * 5), "километров");
    const prCount = yearRuns.filter((run) => run.is_pr).length;
    pushMetric(metrics, "prs", prCount > 0 ? formatInt(prCount) : null, "личных рекордов");
  } else {
    pushMetric(metrics, "runs", formatInt(stats.total_runs ?? 0), "пробежек");
    pushMetric(
      metrics,
      "volunteering",
      stats.total_volunteering ? formatInt(stats.total_volunteering) : null,
      "волонтёрств",
    );
    pushMetric(
      metrics,
      "locations",
      analytics.unique_locations ? formatInt(analytics.unique_locations) : null,
      "локаций",
    );
    pushMetric(
      metrics,
      "distance",
      analytics.total_distance_km ? formatInt(Math.round(analytics.total_distance_km)) : null,
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
    analytics.saturday_streak ? formatInt(analytics.saturday_streak) : null,
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
    analytics.wins_count ? formatInt(analytics.wins_count) : null,
    analytics.wins_scope === "female" ? "побед среди женщин" : "первых мест",
  );
  pushMetric(
    metrics,
    "prs_total",
    analytics.pr_count ? formatInt(analytics.pr_count) : null,
    "личных рекордов",
  );
  pushMetric(
    metrics,
    "regions",
    analytics.unique_run_regions ? formatInt(analytics.unique_run_regions) : null,
    "регионов",
  );
  pushMetric(
    metrics,
    "cities",
    analytics.unique_run_cities ? formatInt(analytics.unique_run_cities) : null,
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
    analytics.avg_position != null ? formatInt(Math.round(analytics.avg_position)) : null,
    "среднее место",
  );
  pushMetric(
    metrics,
    "streak_max",
    analytics.saturday_streak_max ? formatInt(analytics.saturday_streak_max) : null,
    "рекорд серии суббот",
  );
  pushMetric(
    metrics,
    "years",
    analytics.days_since_first_run != null
      ? formatInt(Math.max(1, Math.round(analytics.days_since_first_run / 365)))
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
    analytics.unique_volunteer_roles ? formatInt(analytics.unique_volunteer_roles) : null,
    "ролей волонтёра",
  );
  pushMetric(
    metrics,
    "club",
    analytics.run_clubs_earned?.length
      ? formatInt(analytics.run_clubs_earned[analytics.run_clubs_earned.length - 1])
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

// Во многих названиях город уже стоит словом («Ставрополь Комсомольский
// пруд»), и приписка через точку читалась опиской: «Ставрополь Комсомольский
// пруд · Ставрополь». Сверяем по целым словам, поэтому «Ставропольский» за
// «Ставрополь» не считается — там город допишется как раньше.
function normalizeForCityMatch(value: string): string {
  return value
    .toLowerCase()
    .replace(/ё/g, "е")
    .replace(/[^0-9a-zа-я]+/g, " ")
    .trim();
}

function nameContainsCity(name: string, city: string): boolean {
  const needle = normalizeForCityMatch(city);
  if (!needle) {
    return false;
  }
  return ` ${normalizeForCityMatch(name)} `.includes(` ${needle} `);
}

function locationTitleOf(name: string, city: string | null): string {
  if (!city || nameContainsCity(name, city)) {
    return name;
  }
  return `${name} · ${city}`;
}

function locationTitle(page: LocationPage): string {
  return locationTitleOf(page.name, page.city);
}

export function locationEventSubject(page: LocationPage): ShareSubject | null {
  const last = page.stats.last_event;
  if (!last) {
    return null;
  }
  const stats = page.stats;
  const metrics: ShareMetric[] = [];
  // Порядок = приоритет: первые шесть попадают на широкую карточку. Сверху
  // идёт то, что нельзя узнать из одного протокола, — сравнение с обычной
  // субботой на этой же локации.
  const maleWinner = humanizeName(last.best_male_name);
  const femaleWinner = humanizeName(last.best_female_name);
  const topMilestone = last.milestones?.[0];
  const milestoneName = humanizeName(topMilestone?.name);
  const nextUp = last.one_step?.[0];
  const nextUpName = humanizeName(nextUp?.name);

  // Порядок = приоритет: первые шесть попадают на широкую карточку. Сверху то,
  // чем локации делятся в своих каналах, — сравнение с обычной субботой и люди
  // поимённо (разбор каналов: [[location-channels-content-analysis]]).
  pushMetric(metrics, "attendance_delta", deltaPercent(last.finishers, stats.avg_finishers), "к обычному старту");
  // Победители и юбиляры идут двумя вариантами — с именем и сухим. На карточку
  // по умолчанию встаёт именной, сухой доступен в «Настроить».
  if (maleWinner) {
    pushMetric(metrics, "best_male_named", stripLeadingHours(last.best_male_time_display), `Лучший · ${maleWinner}`, {
      keepLabelCase: true,
    });
  }
  if (femaleWinner) {
    pushMetric(
      metrics,
      "best_female_named",
      stripLeadingHours(last.best_female_time_display),
      `Лучшая · ${femaleWinner}`,
      { keepLabelCase: true },
    );
  }
  pushMetric(metrics, "prs", last.prs ? formatInt(last.prs) : null, "личных рекордов");
  if (topMilestone && milestoneName) {
    pushMetric(metrics, "milestone_named", `${formatInt(topMilestone.count)}-й финиш`, milestoneName, {
      keepLabelCase: true,
    });
  }
  pushMetric(metrics, "volunteers", last.volunteers ? formatInt(last.volunteers) : null, "волонтёров");

  // Дальше — запас для «Настроить».
  // «В шаге до клуба» — единственный сюжет каналов, который смотрит вперёд.
  if (nextUp && nextUpName) {
    pushMetric(metrics, "one_step_named", `${formatInt(nextUp.next)}-й`, `следующий финиш · ${nextUpName}`, {
      keepLabelCase: true,
    });
  }
  if (last.male_finishers != null && last.female_finishers != null) {
    pushMetric(
      metrics,
      "gender_split",
      `${formatInt(last.male_finishers)} / ${formatInt(last.female_finishers)}`,
      "мужчин / женщин",
    );
  }
  // Большое поле бежит медленнее обычного, маленькое — быстрее: разрыв со
  // средним временем локации объясняет цифры этой субботы.
  if (last.avg_time_sec != null && stats.avg_finish_time_sec != null) {
    const gap = last.avg_time_sec - stats.avg_finish_time_sec;
    if (Math.abs(gap) >= 15) {
      pushMetric(
        metrics,
        "pace_delta",
        formatGap(gap),
        gap > 0 ? "медленнее обычного" : "быстрее обычного",
      );
    }
  }
  if (last.milestones && last.milestones.length > 0) {
    pushMetric(
      metrics,
      "milestones_count",
      formatInt(last.milestones.length),
      `${pluralFormRu(last.milestones.length, ["юбилей", "юбилея", "юбилеев"])} на старте`,
    );
  }
  pushMetric(metrics, "best_male", stripLeadingHours(last.best_male_time_display), "лучшее · М");
  pushMetric(metrics, "best_female", stripLeadingHours(last.best_female_time_display), "лучшее · Ж");
  if (last.volunteers && last.finishers) {
    const perVolunteer = Math.round(last.finishers / last.volunteers);
    if (perVolunteer >= 1) {
      pushMetric(
        metrics,
        "runners_per_volunteer",
        formatInt(perVolunteer),
        `${pluralFormRu(perVolunteer, ["бегун", "бегуна", "бегунов"])} на волонтёра`,
      );
    }
  }
  pushMetric(metrics, "avg_time", stripLeadingHours(last.avg_time_display), "среднее время");
  pushMetric(metrics, "debutants", last.debutants ? formatInt(last.debutants) : null, "новичков");
  pushMetric(
    metrics,
    "first_at_location",
    last.first_at_location ? formatInt(last.first_at_location) : null,
    "впервые здесь",
  );
  pushMetric(metrics, "date", formatDate(last.event_date), "дата старта");
  // Контекст площадки: с чем сравнивать цифры этой субботы.
  pushMetric(
    metrics,
    "avg_finishers",
    stats.avg_finishers ? formatInt(Math.round(stats.avg_finishers)) : null,
    "в среднем на старте",
  );
  pushMetric(
    metrics,
    "attendance_record",
    stats.attendance_record ? formatInt(stats.attendance_record.finishers) : null,
    "рекорд посещаемости",
  );

  const attendance = page.stats.attendance_record;
  const isAttendanceRecord = attendance != null && attendance.event_date === last.event_date;

  const data: ShareCardData = {
    audience: "location",
    title: locationTitle(page),
    // Номер старта локации называют все каналы без исключения — ему место
    // в подзаголовке карточки, рядом с датой (плиткой Дмитрий не захотел).
    subtitle: [
      last.event_number != null ? `Старт №${formatInt(last.event_number)}` : null,
      formatDate(last.event_date),
      platformCodeLabel(last.platform_code),
    ]
      .filter(Boolean)
      .join(" · "),
    plate: "ПОСЛЕДНИЙ СТАРТ",
    hero:
      last.finishers != null
        ? { value: formatInt(last.finishers), caption: "финишёров" }
        : undefined,
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

/** Протокол старта: постер «как прошла суббота» с этой страницы протокола. */
export function locationProtocolSubject(protocol: LocationProtocol): ShareSubject {
  const summary = protocol.summary;
  const metrics: ShareMetric[] = [];
  const maleWinner = humanizeName(summary.best_male_runner_name);
  const femaleWinner = humanizeName(summary.best_female_runner_name);

  // Порядок = приоритет (первые попадают на карточку): победители поимённо,
  // дельта к прошлому старту, рекорды и новички — то, чем делятся каналы.
  if (protocol.previous?.finishers != null && summary.finishers) {
    const delta = summary.finishers - protocol.previous.finishers;
    if (delta !== 0) {
      pushMetric(
        metrics,
        "finishers_delta",
        delta > 0 ? `+${formatInt(delta)}` : `−${formatInt(Math.abs(delta))}`,
        "к прошлому старту",
      );
    }
  }
  if (maleWinner) {
    pushMetric(metrics, "best_male_named", stripLeadingHours(summary.best_male_time_display), `Лучший · ${maleWinner}`, {
      keepLabelCase: true,
    });
  }
  if (femaleWinner) {
    pushMetric(
      metrics,
      "best_female_named",
      stripLeadingHours(summary.best_female_time_display),
      `Лучшая · ${femaleWinner}`,
      { keepLabelCase: true },
    );
  }
  pushMetric(metrics, "prs", summary.prs ? formatInt(summary.prs) : null, "личных рекордов");
  const newcomers = summary.debutants + summary.first_at_location;
  pushMetric(metrics, "newcomers", newcomers ? formatInt(newcomers) : null, "новичков");
  pushMetric(metrics, "volunteers", summary.volunteers ? formatInt(summary.volunteers) : null, "волонтёров");
  // Запас для «Настроить».
  if (summary.male && summary.female) {
    pushMetric(
      metrics,
      "gender_split",
      `${formatInt(summary.male)} / ${formatInt(summary.female)}`,
      "мужчин / женщин",
    );
  }
  pushMetric(metrics, "median_time", stripLeadingHours(summary.median_time_display), "медианное время");
  pushMetric(metrics, "avg_time", stripLeadingHours(summary.avg_time_display), "среднее время");
  const groupRecords = protocol.results.filter((row) => row.is_age_group_record).length;
  pushMetric(
    metrics,
    "age_group_records",
    groupRecords ? formatInt(groupRecords) : null,
    groupRecords ? pluralFormRu(groupRecords, ["рекорд группы", "рекорда групп", "рекордов групп"]) : "",
  );
  pushMetric(
    metrics,
    "clubs",
    summary.clubs_count ? formatInt(summary.clubs_count) : null,
    "клубов на старте",
  );
  // Дату и систему карточка уже пишет в подзаголовке — плитками не дублируем
  // (то же решение, что в 9d7d345 для остальных сюжетов).

  const chip = summary.is_attendance_record
    ? "Рекорд посещаемости!"
    : summary.is_course_record_male || summary.is_course_record_female
      ? "Новый рекорд трассы!"
      : undefined;

  const data: ShareCardData = {
    audience: "location",
    title: locationTitleOf(protocol.name, protocol.city),
    subtitle: [
      protocol.event_number != null ? `Старт №${formatInt(protocol.event_number)}` : null,
      formatDate(protocol.event_date),
      platformCodeLabel(protocol.platform_code),
    ]
      .filter(Boolean)
      .join(" · "),
    plate: "ПРОТОКОЛ",
    hero:
      summary.finishers > 0
        ? { value: formatInt(summary.finishers), caption: "финишёров" }
        : undefined,
    chip,
    metrics,
  };
  return {
    kind: "location_protocol",
    data,
    fileName: `run5k-${protocol.slug}-protocol-${protocol.event_date}`,
    defaultFormat: "story",
  };
}

export function locationCardSubject(page: LocationPage): ShareSubject {
  const stats = page.stats;
  const metrics: ShareMetric[] = [];
  const male = stats.course_records?.male;
  const female = stats.course_records?.female;
  const maleName = humanizeName(male?.runner_name);
  const femaleName = humanizeName(female?.runner_name);

  // Доля быстрых финишей — из гистограммы страницы, отдельного запроса не надо.
  const fastShare = (() => {
    const rows = page.histogram?.rows ?? [];
    let total = 0;
    let fast = 0;
    for (const row of rows) {
      total += row.count;
      if (row.start_sec < 1500) {
        fast += row.count;
      }
    }
    // На молодой локации доля скачет от старта к старту — не показываем.
    if (total < 300) {
      return null;
    }
    const pct = Math.round((fast / total) * 100);
    return pct > 0 ? `${pct}%` : null;
  })();

  const ageYears = fullYearsSince(stats.first_event_date);
  const perPerson =
    stats.unique_participants > 0 ? stats.finishers_total / stats.unique_participants : null;
  const volunteersPerEvent =
    stats.events_count > 0 ? Math.round(stats.volunteers_total / stats.events_count) : null;
  const attendanceDate = stats.attendance_record?.event_date;

  // Порядок = приоритет: первые шесть попадают на широкую карточку. Имя
  // рекордсмена в подписи — то, ради чего карточку пересылают в чат локации.
  pushMetric(metrics, "events", stats.events_count ? formatInt(stats.events_count) : null, "стартов");
  pushMetric(
    metrics,
    "participants",
    stats.unique_participants ? formatInt(stats.unique_participants) : null,
    "участников",
  );
  pushMetric(
    metrics,
    "record_male",
    stripLeadingHours(male?.finish_time_display),
    maleName ? `рекорд М · ${maleName}` : "рекорд · М",
    { keepLabelCase: maleName != null },
  );
  pushMetric(
    metrics,
    "record_female",
    stripLeadingHours(female?.finish_time_display),
    femaleName ? `рекорд Ж · ${femaleName}` : "рекорд · Ж",
    { keepLabelCase: femaleName != null },
  );
  pushMetric(
    metrics,
    "attendance",
    stats.attendance_record ? formatInt(stats.attendance_record.finishers) : null,
    attendanceDate ? `рекорд посещаемости · ${formatDate(attendanceDate)}` : "рекорд посещаемости",
  );
  pushMetric(
    metrics,
    "runs_per_person",
    perPerson != null && perPerson >= 1.5 ? formatNumber(Math.round(perPerson * 10) / 10) : null,
    "пробежек на участника",
  );
  pushMetric(
    metrics,
    "finishers",
    stats.finishers_total ? formatInt(stats.finishers_total) : null,
    "финишей",
  );
  pushMetric(
    metrics,
    "age",
    ageYears != null && ageYears >= 1 ? pluralizeRu(ageYears, ["год", "года", "лет"]) : null,
    "истории локации",
  );
  pushMetric(metrics, "fast_share", fastShare, "быстрее 25 минут");
  pushMetric(
    metrics,
    "volunteers_per_event",
    volunteersPerEvent != null && volunteersPerEvent >= 1 ? formatInt(volunteersPerEvent) : null,
    "волонтёров на старте",
  );
  // «Рекорд держится N лет» — только у старых рекордов, иначе это не факт.
  const femaleRecordYears = fullYearsSince(female?.event_date);
  pushMetric(
    metrics,
    "record_female_age",
    femaleRecordYears != null && femaleRecordYears >= 2
      ? pluralizeRu(femaleRecordYears, ["год", "года", "лет"])
      : null,
    "держится рекорд Ж",
  );
  const maleRecordYears = fullYearsSince(male?.event_date);
  pushMetric(
    metrics,
    "record_male_age",
    maleRecordYears != null && maleRecordYears >= 2
      ? pluralizeRu(maleRecordYears, ["год", "года", "лет"])
      : null,
    "держится рекорд М",
  );
  pushMetric(
    metrics,
    "volunteers",
    stats.unique_volunteers ? formatInt(stats.unique_volunteers) : null,
    "волонтёров",
  );
  pushMetric(
    metrics,
    "avg_finishers",
    stats.avg_finishers ? formatInt(Math.round(stats.avg_finishers)) : null,
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
  pushMetric(metrics, "total", formatInt(me.total), board.unit);
  // Разбивка по системам — каждая отдельной плиткой (на выбор в «Настроить»).
  const platformEntries = Object.entries(me.platforms ?? {}).filter(([, cell]) => cell && cell.value > 0);
  for (const [code, cell] of platformEntries.sort((a, b) => b[1].value - a[1].value)) {
    pushMetric(metrics, `platform_${code}`, formatInt(cell.value), platformCodeLabel(code));
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
    me.cities_total != null ? formatInt(me.cities_total) : null,
    "городов",
  );
  pushMetric(
    metrics,
    "regions_total",
    me.regions_total != null ? formatInt(me.regions_total) : null,
    "регионов",
  );
  pushMetric(metrics, "top_role", me.top_role, "любимая роль");
  pushMetric(
    metrics,
    "entrants",
    board.entrants ? formatInt(board.entrants) : null,
    "всего в рейтинге",
  );
  pushMetric(
    metrics,
    "threshold",
    board.threshold ? formatInt(board.threshold) : null,
    "порог попадания",
  );

  // Название рейтинга — крупной плашкой (мелкий подзаголовок не бросался в
  // глаза); «из N участников» — только в подписи героя, без плитки-дубля.
  const data: ShareCardData = {
    audience: "runner",
    title: displayName(user),
    plate: board.title,
    hero: {
      value: `№${formatInt(me.rank)}`,
      caption: board.entrants ? `из ${formatInt(board.entrants)} участников` : undefined,
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
  pushMetric(metrics, "date", formatDate(item.event_date), "дата");
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
    item.parkrun_total_credits != null ? formatInt(item.parkrun_total_credits) : null,
    "всего волонтёрств parkrun",
  );

  const data: ShareCardData = {
    audience: "runner",
    title: displayName(user),
    subtitle: [item.location_name, platformCodeLabel(item.platform_code), formatDate(item.event_date)]
      .filter(Boolean)
      .join(" · "),
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
  pushMetric(
    metrics,
    "runs",
    stats.runs_count > 0 ? formatInt(stats.runs_count) : null,
    "пробежек здесь",
  );
  pushMetric(metrics, "best_time", stripLeadingHours(stats.best_time_display), "лучшее время");
  pushMetric(metrics, "avg_time", stripLeadingHours(stats.avg_time_display), "среднее время");
  pushMetric(
    metrics,
    "volunteering",
    stats.volunteering_count > 0 ? formatInt(stats.volunteering_count) : null,
    "волонтёрств",
  );
  if (stats.rank_by_runs_gender != null) {
    const scope = stats.gender === "female" ? "среди женщин" : stats.gender === "male" ? "среди мужчин" : "в топе";
    pushMetric(metrics, "rank", `№${formatInt(stats.rank_by_runs_gender)}`, `${scope} площадки`);
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
      group.place != null ? `№${formatInt(group.place)}` : null,
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
        ? { value: formatInt(stats.runs_count), caption: pluralFormRu(stats.runs_count, ["пробежка", "пробежки", "пробежек"]) }
        : undefined,
    // Число пробежек уже в герое — плитку-дубль убираем.
    metrics: stats.runs_count > 0 ? metrics.filter((metric) => metric.id !== "runs") : metrics,
    fact:
      stats.total_runs > 0 && stats.runs_count > 0
        ? `${formatInt(stats.runs_count)} из ${formatInt(stats.total_runs)} моих стартов — здесь`
        : undefined,
  };
  return {
    kind: "location_me",
    data,
    fileName: `run5k-${stats.slug}-me`,
    defaultFormat: "story",
  };
}
