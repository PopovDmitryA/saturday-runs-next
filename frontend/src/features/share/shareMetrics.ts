import type { DashboardStats, RunItem, User } from "../../lib/api";
import {
  formatDate,
  formatDuration,
  platformCodeLabel,
  timesLabel,
} from "../../lib/format";
import { SHARE_FIELDS, type ShareFieldId, type SharePeriodId } from "./shareConfig";

export type ShareMetricRow = {
  id: ShareFieldId;
  value: string;
  label: string;
};

export type SharePlatformRow = {
  code: string;
  label: string;
  runs: number;
};

export type ShareMetricsContext = {
  period: SharePeriodId;
  runs: RunItem[];
  currentYear: number;
};

function shareFieldLabel(fieldId: ShareFieldId): string {
  return SHARE_FIELDS.find((field) => field.id === fieldId)?.label ?? fieldId;
}

function yearRuns(runs: RunItem[], year: number): RunItem[] {
  const yearStart = `${year}-01-01`;
  return runs.filter((run) => run.event_date >= yearStart);
}

function topLocationFromRuns(runs: RunItem[]): ShareMetricRow | null {
  if (runs.length === 0) {
    return null;
  }
  const counts = new Map<string, { name: string; platformCode: string; count: number }>();
  for (const run of runs) {
    const key = `${run.platform_code}:${run.location_name}`;
    const existing = counts.get(key);
    if (existing) {
      existing.count += 1;
    } else {
      counts.set(key, {
        name: run.location_name,
        platformCode: run.platform_code,
        count: 1,
      });
    }
  }
  const top = [...counts.values()].sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, "ru"))[0];
  return {
    id: "top_location",
    value: top.name,
    label: `${platformCodeLabel(top.platformCode)} · ${top.count} ${timesLabel(top.count)}`,
  };
}

export function shareUserDisplayName(user: User | null | undefined): string {
  if (!user) {
    return "Участник";
  }
  const custom = user.display_name?.trim();
  if (custom) {
    return custom;
  }
  if (user.telegram_username) {
    return `@${user.telegram_username.replace(/^@/, "")}`;
  }
  return "Участник";
}

export function shareSummaryLine(context: ShareMetricsContext, lastRun: RunItem | null): string | null {
  if (context.period === "year") {
    return `Итоги ${context.currentYear}`;
  }
  if (context.period === "last_run" && lastRun) {
    return `${formatDate(lastRun.event_date)} · ${platformCodeLabel(lastRun.platform_code)}`;
  }
  return null;
}

export function buildPlatformRows(stats: DashboardStats): SharePlatformRow[] {
  const order = ["five_verst", "s95", "parkrun"];
  const byPlatform = stats.by_platform ?? {};
  const rows: SharePlatformRow[] = [];
  for (const code of order) {
    const row = byPlatform[code];
    if (!row || row.runs <= 0) {
      continue;
    }
    rows.push({
      code,
      label: platformCodeLabel(code),
      runs: row.runs,
    });
  }
  for (const [code, row] of Object.entries(byPlatform)) {
    if (order.includes(code) || row.runs <= 0) {
      continue;
    }
    rows.push({ code, label: platformCodeLabel(code), runs: row.runs });
  }
  return rows;
}

function buildLastRunMetrics(lastRun: RunItem): ShareMetricRow[] {
  const positionValue = lastRun.position != null ? String(lastRun.position) : "—";
  const positionLabel = lastRun.is_pr ? "Личный рекорд" : "Место";
  return [
    {
      id: "top_location",
      value: lastRun.location_name,
      label: platformCodeLabel(lastRun.platform_code),
    },
    {
      id: "best_finish_time",
      value: lastRun.finish_time_display ?? "—",
      label: formatDate(lastRun.event_date),
    },
    {
      id: "total_runs",
      value: positionValue,
      label: positionLabel,
    },
  ];
}

function metricValue(
  stats: DashboardStats,
  fieldId: ShareFieldId,
  context: ShareMetricsContext,
): ShareMetricRow | null {
  const analytics = stats.analytics;
  const runsInYear = yearRuns(context.runs, context.currentYear);

  if (context.period === "year") {
    switch (fieldId) {
      case "total_runs":
        return {
          id: fieldId,
          value: String(runsInYear.length),
          label: `Пробежек в ${context.currentYear}`,
        };
      case "total_volunteering":
        return {
          id: fieldId,
          value: String(analytics.volunteering_current_year ?? 0),
          label: `Волонтёрств в ${context.currentYear}`,
        };
      case "unique_locations":
      case "unique_run_locations": {
        const uniqueCount = new Set(runsInYear.map((run) => `${run.platform_code}:${run.location_name}`)).size;
        return {
          id: fieldId,
          value: String(uniqueCount),
          label:
            fieldId === "unique_run_locations"
              ? `Локаций с пробежками в ${context.currentYear}`
              : `Уникальных локаций в ${context.currentYear}`,
        };
      }
      case "total_distance_km": {
        const km = runsInYear.length * 5;
        return { id: fieldId, value: String(km), label: `Километров в ${context.currentYear}` };
      }
      case "runs_current_year":
        return {
          id: fieldId,
          value: String(runsInYear.length),
          label: shareFieldLabel(fieldId),
        };
      case "runs_last_12_months":
        return {
          id: fieldId,
          value: String(analytics.runs_last_12_months ?? 0),
          label: shareFieldLabel(fieldId),
        };
      case "pr_count":
        return {
          id: fieldId,
          value: String(runsInYear.filter((run) => run.is_pr).length),
          label: `Личных рекордов в ${context.currentYear}`,
        };
      case "best_finish_time": {
        const timedRuns = runsInYear.filter((run) => run.finish_time_sec != null);
        if (timedRuns.length === 0) {
          return { id: fieldId, value: "—", label: `Лучшее время в ${context.currentYear}` };
        }
        const best = timedRuns.reduce((min, run) =>
          (run.finish_time_sec ?? Number.MAX_SAFE_INTEGER) < (min.finish_time_sec ?? Number.MAX_SAFE_INTEGER)
            ? run
            : min,
        );
        return {
          id: fieldId,
          value: best.finish_time_display ?? formatDuration(best.finish_time_sec ?? 0),
          label: `Лучшее время в ${context.currentYear}`,
        };
      }
      case "top_location":
        return topLocationFromRuns(runsInYear);
      default:
        return null;
    }
  }

  switch (fieldId) {
    case "total_runs":
      return {
        id: fieldId,
        value: String(stats.total_runs ?? 0),
        label: shareFieldLabel(fieldId),
      };
    case "total_volunteering":
      return {
        id: fieldId,
        value: String(stats.total_volunteering ?? 0),
        label: shareFieldLabel(fieldId),
      };
    case "unique_locations":
      return {
        id: fieldId,
        value: String(analytics.unique_locations ?? 0),
        label: shareFieldLabel(fieldId),
      };
    case "unique_run_locations":
      return {
        id: fieldId,
        value: String(analytics.unique_run_locations ?? 0),
        label: shareFieldLabel(fieldId),
      };
    case "total_distance_km": {
      const km = Math.round(analytics.total_distance_km ?? 0);
      return { id: fieldId, value: String(km), label: "Километров пробега" };
    }
    case "saturday_streak":
      return {
        id: fieldId,
        value: String(analytics.saturday_streak ?? 0),
        label: shareFieldLabel(fieldId),
      };
    case "saturday_consistency": {
      const pct = analytics.saturday_consistency_pct;
      if (pct == null) {
        return { id: fieldId, value: "—", label: shareFieldLabel(fieldId) };
      }
      return {
        id: fieldId,
        value: `${Math.round(pct)}%`,
        label: `${analytics.saturday_consistency_active} из ${analytics.saturday_consistency_total} суббот`,
      };
    }
    case "pr_count":
      return {
        id: fieldId,
        value: String(analytics.pr_count ?? 0),
        label: shareFieldLabel(fieldId),
      };
    case "best_finish_time":
      return {
        id: fieldId,
        value:
          analytics.best_finish_time_sec != null
            ? formatDuration(analytics.best_finish_time_sec)
            : "—",
        label: shareFieldLabel(fieldId),
      };
    case "runs_current_year":
      return {
        id: fieldId,
        value: String(analytics.runs_current_year ?? 0),
        label: shareFieldLabel(fieldId),
      };
    case "runs_last_12_months":
      return {
        id: fieldId,
        value: String(analytics.runs_last_12_months ?? 0),
        label: shareFieldLabel(fieldId),
      };
    case "volunteering_index":
      return {
        id: fieldId,
        value: analytics.volunteering_index ?? "—",
        label: shareFieldLabel(fieldId),
      };
    case "top_location": {
      const top = analytics.top_location;
      if (!top) {
        return { id: fieldId, value: "—", label: shareFieldLabel(fieldId) };
      }
      const visits =
        top.tied_count > 1 ? ` · ${top.count} ${timesLabel(top.count)}` : top.count > 0 ? ` · ${top.count} стартов` : "";
      return {
        id: fieldId,
        value: top.name,
        label: `${platformCodeLabel(top.platform_code)}${visits}`,
      };
    }
    case "top_volunteer_role": {
      const role = analytics.top_volunteer_role;
      if (!role) {
        return { id: fieldId, value: "—", label: shareFieldLabel(fieldId) };
      }
      return {
        id: fieldId,
        value: role.role,
        label: `${role.count} ${timesLabel(role.count)} на стартах`,
      };
    }
    case "next_run_club": {
      const next = analytics.next_run_club;
      if (next == null) {
        const clubs = analytics.run_clubs_earned ?? [];
        const last = clubs.length > 0 ? clubs[clubs.length - 1] : null;
        return {
          id: fieldId,
          value: last != null ? `${last}+` : "—",
          label: "Достигнутый клуб",
        };
      }
      const toGo = analytics.runs_to_next_milestone;
      return {
        id: fieldId,
        value: String(next),
        label: toGo != null ? `Осталось ${toGo} до клуба` : "Следующий клуб",
      };
    }
    default:
      return null;
  }
}

export function defaultPrimaryFieldId(fields: ShareFieldId[]): ShareFieldId | null {
  if (fields.length === 0) {
    return null;
  }
  if (fields.length >= 3) {
    return fields[1];
  }
  if (fields.length === 2) {
    return fields[1];
  }
  return fields[0];
}

/** Top / center (primary) / bottom slots for share cards. */
export function layoutMetricZones(
  fields: ShareFieldId[],
  primaryFieldId: ShareFieldId | null,
  resolveRow: (fieldId: ShareFieldId) => ShareMetricRow | null,
): (ShareMetricRow | null)[] {
  const zones: (ShareMetricRow | null)[] = [null, null, null];
  if (fields.length === 0) {
    return zones;
  }

  const primaryId =
    primaryFieldId && fields.includes(primaryFieldId)
      ? primaryFieldId
      : defaultPrimaryFieldId(fields);
  if (!primaryId) {
    return zones;
  }

  const others = fields.filter((id) => id !== primaryId);
  zones[1] = resolveRow(primaryId);
  if (others[0]) {
    zones[0] = resolveRow(others[0]);
  }
  if (others[1]) {
    zones[2] = resolveRow(others[1]);
  }
  return zones;
}

export const LAST_RUN_METRIC_FIELDS: ShareFieldId[] = [
  "top_location",
  "best_finish_time",
  "total_runs",
];

export const LAST_RUN_PRIMARY_FIELD: ShareFieldId = "best_finish_time";

export function buildShareMetrics(
  stats: DashboardStats,
  fieldIds: ShareFieldId[],
  context: ShareMetricsContext,
  lastRun: RunItem | null,
): ShareMetricRow[] {
  if (context.period === "last_run") {
    return lastRun ? buildLastRunMetrics(lastRun) : [];
  }

  const rows: ShareMetricRow[] = [];
  for (const id of fieldIds) {
    const row = metricValue(stats, id, context);
    if (row) {
      rows.push(row);
    }
  }
  return rows;
}

export function getLastRun(runs: RunItem[]): RunItem | null {
  return runs[0] ?? null;
}
