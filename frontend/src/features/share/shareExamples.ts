import type { DashboardStats, RunItem } from "../../lib/api";
import { SHARE_STOCK_BACKGROUNDS, type ShareBackground } from "./shareBackgrounds";
import {
  SHARE_PRESETS,
  type SharePeriodId,
  type SharePreset,
  type ShareTextColor,
} from "./shareConfig";
import {
  buildShareMetrics,
  getLastRun,
  LAST_RUN_METRIC_FIELDS,
  LAST_RUN_PRIMARY_FIELD,
  layoutMetricZones,
  shareSummaryLine,
  type ShareMetricRow,
} from "./shareMetrics";

export type ShareGalleryExample = {
  id: string;
  preset: SharePreset;
  background: ShareBackground;
  textColor: ShareTextColor;
  period: SharePeriodId;
  displayName: string;
  summaryLine: string | null;
  metrics: (ShareMetricRow | null)[];
};

type BuildExamplesInput = {
  stats: DashboardStats;
  runs: RunItem[];
  displayName: string;
  currentYear: number;
  count?: number;
};

function pickRandom<T>(items: T[]): T {
  return items[Math.floor(Math.random() * items.length)]!;
}

function shufflePick<T>(items: T[], count: number): T[] {
  const copy = [...items];
  for (let i = copy.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j]!, copy[i]!];
  }
  return copy.slice(0, Math.min(count, copy.length));
}

function pickTextColor(): ShareTextColor {
  return pickRandom(["white", "black", "cream", "gold", "mint", "sky", "coral"]);
}

function availablePeriods(lastRun: RunItem | null): SharePeriodId[] {
  const periods: SharePeriodId[] = ["all_time", "year"];
  if (lastRun) {
    periods.push("last_run");
  }
  return periods;
}

function buildSingleExample(
  stats: DashboardStats,
  runs: RunItem[],
  displayName: string,
  currentYear: number,
  preset: SharePreset,
  background: ShareBackground,
  period: SharePeriodId,
): ShareGalleryExample | null {
  const lastRun = getLastRun(runs);
  const context = { period, runs, currentYear };

  const layoutFields = period === "last_run" ? LAST_RUN_METRIC_FIELDS : preset.fields;
  const primaryFieldId =
    period === "last_run" ? LAST_RUN_PRIMARY_FIELD : (preset.primaryFieldId ?? null);
  const fieldIds = period === "last_run" ? [] : preset.fields;

  const metricRows = buildShareMetrics(stats, fieldIds, context, lastRun);
  const rowById = new Map(metricRows.map((row) => [row.id, row]));
  const metrics = layoutMetricZones(layoutFields, primaryFieldId, (fieldId) => rowById.get(fieldId) ?? null);

  if (!metrics.some((metric) => metric != null)) {
    return null;
  }

  return {
    id: `${preset.id}-${background.id}-${period}-${Math.random().toString(36).slice(2, 8)}`,
    preset,
    background,
    textColor: pickTextColor(),
    period,
    displayName,
    summaryLine: shareSummaryLine(context, lastRun),
    metrics,
  };
}

export function buildRandomShareExamples({
  stats,
  runs,
  displayName,
  currentYear,
  count = 3,
}: BuildExamplesInput): ShareGalleryExample[] {
  const presets = SHARE_PRESETS.filter((preset) => preset.id !== "custom");
  const periods = availablePeriods(getLastRun(runs));
  const results: ShareGalleryExample[] = [];
  let attempts = 0;

  while (results.length < count && attempts < 60) {
    attempts += 1;
    const preset = pickRandom(presets);
    const background = pickRandom(SHARE_STOCK_BACKGROUNDS);
    const period = pickRandom(periods);
    const example = buildSingleExample(
      stats,
      runs,
      displayName,
      currentYear,
      preset,
      background,
      period,
    );
    if (!example) {
      continue;
    }
    const duplicate = results.some(
      (item) =>
        item.preset.id === example.preset.id &&
        item.period === example.period &&
        item.background.id === example.background.id,
    );
    if (duplicate) {
      continue;
    }
    results.push(example);
  }

  if (results.length >= count) {
    return results;
  }

  const presetPool = shufflePick(presets, presets.length);
  const backgroundPool = shufflePick(SHARE_STOCK_BACKGROUNDS, SHARE_STOCK_BACKGROUNDS.length);
  for (const preset of presetPool) {
    for (const background of backgroundPool) {
      for (const period of periods) {
        if (results.length >= count) {
          return results;
        }
        const example = buildSingleExample(
          stats,
          runs,
          displayName,
          currentYear,
          preset,
          background,
          period,
        );
        if (!example) {
          continue;
        }
        const duplicate = results.some((item) => item.id === example.id);
        if (duplicate) {
          continue;
        }
        results.push(example);
      }
    }
  }

  return results;
}
