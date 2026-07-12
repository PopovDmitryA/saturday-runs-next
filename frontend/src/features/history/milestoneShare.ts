import type { MyHistoryMilestone, RunItem } from "../../lib/api";
import { formatDateLong, platformCodeLabel, pluralFormRu } from "../../lib/format";

// Ключ, по которому «Моя история» передаёт веху в мастер «Поделиться»
// (SharePage читает его при ?story=milestone).
export const MILESTONE_SHARE_KEY = "shareMyHistoryMilestone";

// Что кладём в sessionStorage: пробежка-носитель вехи (для однопробежечного
// рендера постера) + текст акцент-плашки.
export type MilestoneSharePayload = {
  accent_label: string;
  run: RunItem;
};

const VOLUNTEER_FORMS = ["волонтёрство", "волонтёрства", "волонтёрств"] as const;

// «10-я» / «25-я» — все юбилейные номера пробежек женского рода.
export function runNumberLabel(number: number): string {
  return `${number}-я`;
}

// «10-е волонтёрство» — средний род.
export function volunteerNumberLabel(number: number): string {
  return `${number}-е`;
}

// Номер региона/города, если он юбилейный (3, 5, 10, 15, 20…), иначе null.
function geoMilestoneNumber(number: number | null): number | null {
  if (number == null) {
    return null;
  }
  const isMilestone = number === 3 || number === 5 || (number >= 10 && number % 5 === 0);
  return isMilestone ? number : null;
}

export function milestoneAccentLabel(milestone: MyHistoryMilestone): string {
  switch (milestone.kind) {
    case "first_run":
      return "Первая пробежка";
    case "first_run_platform":
      return "Первый старт в системе";
    case "run_club":
      return `Клуб ${milestone.number ?? ""}`.trim();
    case "run_club_platform":
      return `Клуб ${milestone.number ?? ""} в системе`.trim();
    case "location_club":
      return `${runNumberLabel(milestone.number ?? 0)} на локации`;
    case "volunteer_location_club":
      return `${volunteerNumberLabel(milestone.number ?? 0)} на локации`;
    case "first_foreign_parkrun":
      return "Первый зарубежный паркран";
    case "first_foreign_run":
      return "Первый зарубежный старт";
    case "global_pr":
      return "Глобальный рекорд";
    case "pr":
      return "Личный рекорд";
    case "new_region": {
      const milestoneNumber = geoMilestoneNumber(milestone.number);
      if (milestoneNumber != null) {
        return `${milestoneNumber}-й регион${milestone.region ? ` · ${milestone.region}` : ""}`;
      }
      return milestone.region ? `Новый регион · ${milestone.region}` : "Новый регион";
    }
    case "new_city": {
      const city = milestone.location_city;
      const milestoneNumber = geoMilestoneNumber(milestone.number);
      if (milestoneNumber != null) {
        return `${milestoneNumber}-й город${city ? ` · ${city}` : ""}`;
      }
      return city ? `Новый город · ${city}` : "Новый город";
    }
    case "new_country":
      return milestone.country ? `Новая страна · ${milestone.country}` : "Новая страна";
    case "new_location": {
      const milestoneNumber = geoMilestoneNumber(milestone.number);
      return milestoneNumber != null ? `${milestoneNumber}-я новая локация` : "Новая локация";
    }
    case "first_volunteer":
      return "Первое волонтёрство";
    case "volunteer_club":
      return `${volunteerNumberLabel(milestone.number ?? 0)} волонтёрство`;
    case "volunteer_club_platform":
      return `${volunteerNumberLabel(milestone.number ?? 0)} волонтёрство в системе`;
    default:
      return "Веха";
  }
}

export function volunteeringCountPhrase(count: number): string {
  return `${count} ${pluralFormRu(count, VOLUNTEER_FORMS)}`;
}

// Веха → RunItem для однопробежечного рендера постера (как onThisDayRunToRunItem).
export function milestoneToRunItem(milestone: MyHistoryMilestone): RunItem {
  return {
    platform_code: milestone.platform_code,
    event_date: milestone.event_date,
    event_number: null,
    location_name: milestone.location_name,
    location_city: milestone.location_city,
    location_country: null,
    position: milestone.position,
    gender_position: milestone.gender_position,
    finish_time_display: milestone.finish_time_display,
    finish_time_sec: milestone.finish_time_sec,
    pace_display: milestone.pace_display,
    pace_sec_per_km: null,
    age_category: null,
    is_pr: milestone.kind === "pr" || milestone.kind === "global_pr",
    is_global_pr: milestone.is_global_pr,
    is_crosslinked: false,
    is_first_run: milestone.kind === "first_run",
    is_first_run_at_location: false,
    club_name: null,
    achievement_labels: [],
    status: null,
    is_test_event: false,
    event_url: milestone.event_url,
  };
}

// «00:26:43» → «26:43».
function compactTime(display: string | null): string | null {
  if (!display) {
    return null;
  }
  return display.replace(/^00:/, "");
}

// «−52 сек» / «−1:44» — на сколько улучшен рекорд (как в HistoryPage).
function deltaLabel(deltaSec: number): string {
  if (deltaSec < 60) {
    return `−${deltaSec} сек`;
  }
  const minutes = Math.floor(deltaSec / 60);
  const seconds = deltaSec % 60;
  return `−${minutes}:${String(seconds).padStart(2, "0")}`;
}

// Строка деталей под заголовком: дата и локация всегда, остальное — если
// применимо к конкретной вехе (время/место/улучшение/роль).
function milestoneDetailsLine(
  milestone: MyHistoryMilestone,
  options: {
    time?: boolean;
    position?: boolean;
    delta?: boolean;
    role?: boolean;
    platformInLocation?: boolean;
  } = {},
): string {
  const platform = platformCodeLabel(milestone.platform_code);
  const time = compactTime(milestone.finish_time_display);
  const parts = [
    `📅 ${formatDateLong(milestone.event_date)}`,
    `📍 ${milestone.location_name}${options.platformInLocation === false ? "" : ` (${platform})`}`,
  ];
  if (options.time && time) {
    parts.push(`⏱ ${time}`);
  }
  if (options.position && milestone.position != null) {
    parts.push(`${milestone.position} место`);
  }
  if (options.delta && milestone.delta_sec != null) {
    parts.push(`улучшение ${deltaLabel(milestone.delta_sec)}`);
  }
  if (options.role && milestone.role) {
    parts.push(`роль: ${milestone.role}`);
  }
  return parts.join(" · ");
}

// Текст-хвастовство для пересылки (в Telegram и т.п.): заголовок достижения +
// строка деталей (дата, локация, время/место — где применимо) + ссылка на
// сайт. В отличие от accent-плашки на постере — развёрнутый, без сокращений.
export function milestoneBragText(milestone: MyHistoryMilestone, siteUrl: string): string {
  const platform = platformCodeLabel(milestone.platform_code);
  const geoNumber = geoMilestoneNumber(milestone.number);

  let headline: string;
  let details: string;
  switch (milestone.kind) {
    case "first_run":
      headline = "🏁 Моя первая пробежка в истории!";
      details = milestoneDetailsLine(milestone, { time: true, position: true });
      break;
    case "first_run_platform":
      headline = `🚩 Первый старт в системе «${platform}»!`;
      details = milestoneDetailsLine(milestone, { time: true, position: true, platformInLocation: false });
      break;
    case "run_club":
      headline = `🏅 ${milestone.number}-я пробежка — вступил в клуб ${milestone.number}!`;
      details = milestoneDetailsLine(milestone, { time: true, position: true });
      break;
    case "run_club_platform":
      headline = `🎖️ ${milestone.number}-я пробежка в системе «${platform}» — свой клуб в этой системе!`;
      details = milestoneDetailsLine(milestone, { time: true, position: true, platformInLocation: false });
      break;
    case "location_club":
      headline = `🎯 ${runNumberLabel(milestone.number ?? 0)} пробежка на локации «${milestone.location_name}» — свой клуб там!`;
      details = milestoneDetailsLine(milestone, { time: true, position: true });
      break;
    case "volunteer_location_club":
      headline = `📍 ${volunteerNumberLabel(milestone.number ?? 0)} волонтёрство на локации «${milestone.location_name}» — свой клуб там!`;
      details = milestoneDetailsLine(milestone, { role: true });
      break;
    case "global_pr":
      headline = "🏆 Новый глобальный рекорд!";
      details = milestoneDetailsLine(milestone, { time: true, position: true, delta: true });
      break;
    case "pr":
      headline = `⚡ Новый личный рекорд в системе «${platform}»!`;
      details = milestoneDetailsLine(milestone, { time: true, position: true, delta: true, platformInLocation: false });
      break;
    case "first_foreign_parkrun":
      headline = "✈️ Первый зарубежный паркран!";
      details = milestoneDetailsLine(milestone, { time: true, position: true });
      break;
    case "first_foreign_run":
      headline = `✈️ Первый зарубежный старт${milestone.country ? ` — ${milestone.country}` : ""}!`;
      details = milestoneDetailsLine(milestone, { time: true, position: true });
      break;
    case "new_country":
      headline = `🌍 Новая страна на моей карте — ${milestone.country ?? milestone.location_name}!`;
      details = milestoneDetailsLine(milestone, { time: true, position: true });
      break;
    case "new_region":
      headline = geoNumber != null
        ? `🧭 ${geoNumber}-й регион на моей карте — ${milestone.region ?? milestone.location_name}!`
        : `🧭 Новый регион на моей карте — ${milestone.region ?? milestone.location_name}!`;
      details = milestoneDetailsLine(milestone, { time: true, position: true });
      break;
    case "new_city":
      headline = geoNumber != null
        ? `🏙️ ${geoNumber}-й город на моей карте — ${milestone.location_city ?? milestone.location_name}!`
        : `🏙️ Новый город на моей карте — ${milestone.location_city ?? milestone.location_name}!`;
      details = milestoneDetailsLine(milestone, { time: true, position: true });
      break;
    case "new_location":
      headline = geoNumber != null
        ? `🗺️ ${geoNumber}-я новая локация — ${milestone.location_name}!`
        : `🗺️ Новая локация на моей карте — ${milestone.location_name}!`;
      details = milestoneDetailsLine(milestone, { time: true, position: true });
      break;
    case "first_volunteer":
      headline = "🤝 Впервые волонтёрил!";
      details = milestoneDetailsLine(milestone, { role: true });
      break;
    case "volunteer_club":
      headline = `🤝 ${milestone.number}-е волонтёрство — свой клуб волонтёров!`;
      details = milestoneDetailsLine(milestone, { role: true });
      break;
    case "volunteer_club_platform":
      headline = `🙌 ${milestone.number}-е волонтёрство в системе «${platform}»!`;
      details = milestoneDetailsLine(milestone, { role: true, platformInLocation: false });
      break;
    default:
      headline = "⭐ Новая веха в моей беговой истории!";
      details = milestoneDetailsLine(milestone);
  }

  return `${headline}\n${details}\n\nИсточник: ${siteUrl}`;
}

export function storeMilestoneShare(milestone: MyHistoryMilestone): void {
  const payload: MilestoneSharePayload = {
    accent_label: milestoneAccentLabel(milestone),
    run: milestoneToRunItem(milestone),
  };
  try {
    sessionStorage.setItem(MILESTONE_SHARE_KEY, JSON.stringify(payload));
  } catch {
    // приватный режим/переполнение — мастер откроется без пресета, не критично
  }
}
