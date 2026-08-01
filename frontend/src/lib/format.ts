export function formatDate(value: string): string {
  const date = parseIsoDate(value) ?? new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleDateString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

const MONTHS_GENITIVE = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
] as const;

/** «2023-07-02» → «2 июля 2023». */
export function formatDateLong(value: string): string {
  const date = parseIsoDate(value) ?? new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return `${date.getDate()} ${MONTHS_GENITIVE[date.getMonth()]} ${date.getFullYear()}`;
}

/** ISO YYYY-MM-DD without UTC timezone shift (for chart axis labels). */
export function parseIsoDate(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) {
    return null;
  }
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

export function formatChartDay(value: string): string {
  const date = parseIsoDate(value);
  if (!date || Number.isNaN(date.getTime())) {
    return value.slice(5).replace("-", ".");
  }
  return date.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function profileDataFreshnessLines(
  dataUpdatedAt: string | null | undefined,
  dataThroughDate: string | null | undefined,
): { updatedLine: string | null; throughLine: string | null } {
  const updatedLine = dataUpdatedAt
    ? `Данные в базе обновлены: ${formatDateTime(dataUpdatedAt)}`
    : null;
  const throughLine = dataThroughDate
    ? `Актуально по пробежкам и волонтёрству до: ${formatDate(dataThroughDate)}`
    : dataUpdatedAt
      ? "История пробежек в базе пока пуста — после привязки подтянем недостающее."
      : null;
  return { updatedLine, throughLine };
}

export function formatDurationShort(seconds: number | null | undefined): string {
  if (seconds == null || seconds <= 0) {
    return "истекло";
  }
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) {
    return `${days} д ${hours} ч`;
  }
  if (hours > 0) {
    return `${hours} ч ${minutes} мин`;
  }
  return `${minutes} мин`;
}

export function formatDuration(totalSec: number): string {
  const hours = Math.floor(totalSec / 3600);
  const minutes = Math.floor((totalSec % 3600) / 60);
  const seconds = Math.floor(totalSec % 60);
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

const FINISH_TIME_RE = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/;

export function formatFinishTimeValue(
  finishTimeDisplay: string | null | undefined,
  finishTimeSec: number | null | undefined,
): string {
  if (finishTimeSec != null) {
    return formatDuration(finishTimeSec);
  }
  if (!finishTimeDisplay) {
    return "—";
  }
  const cleaned = finishTimeDisplay.trim();
  const match = FINISH_TIME_RE.exec(cleaned);
  if (!match) {
    return cleaned;
  }
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  const seconds = match[3] != null ? Number(match[3]) : 0;
  const totalSec =
    match[3] == null && hours < 60 ? hours * 60 + minutes : hours * 3600 + minutes * 60 + seconds;
  return formatDuration(totalSec);
}

export function formatPace(secPerKm: number): string {
  const minutes = Math.floor(secPerKm / 60);
  const seconds = Math.floor(secPerKm % 60);
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function formatMonthShort(monthKey: string): string {
  const [year, month] = monthKey.split("-");
  const parsed = new Date(Number(year), Number(month) - 1, 1);
  if (Number.isNaN(parsed.getTime())) {
    return monthKey;
  }
  return parsed.toLocaleDateString("ru-RU", { month: "short" });
}

export function formatMonthYear(monthKey: string): string {
  const [year, month] = monthKey.split("-");
  const parsed = new Date(Number(year), Number(month) - 1, 1);
  if (Number.isNaN(parsed.getTime())) {
    return monthKey;
  }
  return parsed.toLocaleDateString("ru-RU", { month: "short", year: "2-digit" });
}

/** Russian plural form: one / few (2–4) / many (0, 5–20, …). */
export function pluralFormRu(count: number, forms: readonly [string, string, string]): string {
  const abs = Math.abs(Math.trunc(count));
  const mod10 = abs % 10;
  const mod100 = abs % 100;
  if (mod10 === 1 && mod100 !== 11) {
    return forms[0];
  }
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) {
    return forms[1];
  }
  return forms[2];
}

export function pluralizeRu(count: number, forms: readonly [string, string, string]): string {
  return `${count} ${pluralFormRu(count, forms)}`;
}

const LOCATION_CAP_FORMS = ["Локация", "Локации", "Локаций"] as const;
const REGION_CAP_FORMS = ["Регион", "Региона", "Регионов"] as const;
const CITY_CAP_FORMS = ["Город", "Города", "Городов"] as const;
const RUN_FORMS = ["пробежка", "пробежки", "пробежек"] as const;
const RUN_CAP_FORMS = ["Пробежка", "Пробежки", "Пробежек"] as const;
const VOLUNTEERING_FORMS = ["волонтёрство", "волонтёрства", "волонтёрств"] as const;
const VOLUNTEERING_CAP_FORMS = ["Волонтёрство", "Волонтёрства", "Волонтёрств"] as const;
const ROLE_CAP_FORMS = ["Роль", "Роли", "Ролей"] as const;
const TIME_FORMS = ["раз", "раза", "раз"] as const;
const DAY_CAP_FORMS = ["День", "Дня", "Дней"] as const;
const PR_RUN_FORMS = ["PR-пробежка", "PR-пробежки", "PR-пробежек"] as const;
const WIN_CAP_FORMS = ["Победа", "Победы", "Побед"] as const;
const SATURDAY_FORMS = ["суббота", "субботы", "суббот"] as const;
const VISIT_FORMS = ["визит", "визита", "визитов"] as const;

export function locationsWithRunsLabel(count: number): string {
  return `${pluralFormRu(count, LOCATION_CAP_FORMS)} с пробежками`;
}

export function locationsWithVolunteeringLabel(count: number): string {
  return `${pluralFormRu(count, LOCATION_CAP_FORMS)} с волонтёрством`;
}

export function regionsWithRunsLabel(count: number): string {
  return `${pluralFormRu(count, REGION_CAP_FORMS)} с пробежками`;
}

export function citiesWithRunsLabel(count: number): string {
  return `${pluralFormRu(count, CITY_CAP_FORMS)} с пробежками`;
}

export function regionsWithVolunteeringLabel(count: number): string {
  return `${pluralFormRu(count, REGION_CAP_FORMS)} с волонтёрством`;
}

export function citiesWithVolunteeringLabel(count: number): string {
  return `${pluralFormRu(count, CITY_CAP_FORMS)} с волонтёрством`;
}

export function locationsWithRunsSummary(count: number): string {
  return pluralizeRu(count, ["локация с пробежками", "локации с пробежками", "локаций с пробежками"]);
}

export function locationsWithVolunteeringSummary(count: number): string {
  return pluralizeRu(count, [
    "локация с волонтёрством",
    "локации с волонтёрством",
    "локаций с волонтёрством",
  ]);
}

export function uniqueLocationsTitle(count: number): string {
  return pluralFormRu(count, ["Уникальная локация", "Уникальные локации", "Уникальных локаций"]);
}

export function uniqueLocationsLabel(count: number): string {
  return pluralizeRu(count, ["уникальная локация", "уникальные локации", "уникальных локаций"]);
}

export function runsCapLabel(count: number): string {
  return pluralFormRu(count, RUN_CAP_FORMS);
}

export function volunteeringCapLabel(count: number): string {
  return pluralFormRu(count, VOLUNTEERING_CAP_FORMS);
}

export function rolesCapLabel(count: number): string {
  return pluralFormRu(count, ROLE_CAP_FORMS);
}

export function timesLabel(count: number): string {
  return pluralFormRu(count, TIME_FORMS);
}

export function daysCapLabel(count: number): string {
  return pluralFormRu(count, DAY_CAP_FORMS);
}

export function prRunsLabel(count: number): string {
  return pluralFormRu(count, PR_RUN_FORMS);
}

export function winsCapLabel(count: number): string {
  return pluralFormRu(count, WIN_CAP_FORMS);
}

export function saturdaysLabel(count: number): string {
  return pluralFormRu(count, SATURDAY_FORMS);
}

export function visitsLabel(count: number): string {
  return pluralizeRu(count, VISIT_FORMS);
}

const KM_FORMS = ["километр", "километра", "километров"] as const;
const CLUB_FORMS = ["клуб", "клуба", "клубов"] as const;

export function kilometersLabel(count: number): string {
  return pluralizeRu(count, KM_FORMS);
}

export function runClubsLabel(count: number): string {
  return pluralFormRu(count, CLUB_FORMS);
}

export function formatVsFieldPct(value: number): string {
  const abs = Math.abs(value);
  const formatted = Number.isInteger(abs) ? String(abs) : abs.toFixed(1);
  if (value > 0) {
    return `На ${formatted}% быстрее среднего`;
  }
  if (value < 0) {
    return `На ${formatted}% медленнее среднего`;
  }
  return "Как среднее по старту";
}

export function volunteerRolesLabel(count: number): string {
  return `${pluralFormRu(count, ROLE_CAP_FORMS)} волонтёра`;
}

export function platformCodeLabel(code: string): string {
  const labels: Record<string, string> = {
    five_verst: "5 вёрст",
    s95: "С95",
    parkrun: "parkrun",
    runpark: "RunPark",
  };
  return labels[code] ?? code;
}

type PlatformActivityStats = {
  runs: number;
  volunteering: number;
};

export function profilePlatformSummaryLabel(
  displayName: string,
  stats?: PlatformActivityStats,
): string {
  const parts = [displayName];
  const activityParts: string[] = [];
  if (stats && stats.runs > 0) {
    activityParts.push(pluralizeRu(stats.runs, RUN_FORMS));
  }
  if (stats && stats.volunteering > 0) {
    activityParts.push(pluralizeRu(stats.volunteering, VOLUNTEERING_FORMS));
  }
  if (activityParts.length > 0) {
    parts.push(activityParts.join(", "));
  }
  return parts.join(" · ");
}

export function platformSpoilerLabel(
  code: string,
  displayName?: string | null,
  stats?: PlatformActivityStats,
): string {
  const title = platformCodeLabel(code);
  if (!displayName) {
    return title;
  }

  const parts = [title, displayName];
  const activityParts: string[] = [];
  if (stats && stats.runs > 0) {
    activityParts.push(pluralizeRu(stats.runs, RUN_FORMS));
  }
  if (stats && stats.volunteering > 0) {
    activityParts.push(pluralizeRu(stats.volunteering, VOLUNTEERING_FORMS));
  }
  if (activityParts.length > 0) {
    parts.push(activityParts.join(", "));
  }
  return parts.join(" · ");
}

export function syncTriggerLabel(trigger: string): string {
  const labels: Record<string, string> = {
    login: "Автообновление при входе",
    manual: "Ручное обновление",
    linking: "Привязка профиля",
  };
  return labels[trigger] ?? trigger;
}

export function celeryStateLabel(state: string): string {
  const labels: Record<string, string> = {
    PENDING: "В очереди",
    STARTED: "Выполняется",
    RETRY: "Повтор",
    SUCCESS: "Готово",
    FAILURE: "Ошибка",
    REVOKED: "Отменено",
  };
  return labels[state] ?? state;
}

export function syncStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    idle: "Ожидание",
    syncing: "Обновление…",
    ok: "Актуально",
    error: "Ошибка",
    queued: "В очереди",
    running: "Выполняется",
    success: "Готово",
    failed: "Ошибка",
    partial: "Частично",
  };
  return labels[status] ?? status;
}

/**
 * «3 дня назад», «1 час назад» — возраст события для лент, где точная дата
 * второстепенна (бэклог). Точную дату показываем в подсказке рядом.
 */
export function formatRelativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) {
    return "";
  }
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 60) {
    return "только что";
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) {
    return `${minutes} ${pluralFormRu(minutes, ["минуту", "минуты", "минут"])} назад`;
  }
  const hours = Math.round(minutes / 60);
  if (hours < 24) {
    return `${hours} ${pluralFormRu(hours, ["час", "часа", "часов"])} назад`;
  }
  const days = Math.round(hours / 24);
  if (days < 31) {
    return `${days} ${pluralFormRu(days, ["день", "дня", "дней"])} назад`;
  }
  const months = Math.round(days / 30.44);
  if (months < 12) {
    return `${months} ${pluralFormRu(months, ["месяц", "месяца", "месяцев"])} назад`;
  }
  const years = Math.round(months / 12);
  return `${years} ${pluralFormRu(years, ["год", "года", "лет"])} назад`;
}
