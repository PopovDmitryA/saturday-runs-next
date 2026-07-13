import { ChartColumnTooltip } from "./ChartColumnTooltip";
import { formatDate, platformCodeLabel, pluralizeRu, saturdaysLabel } from "../lib/format";

type CalendarItem = { platform_code: string; location: string };

type CalendarDay = {
  date: string;
  runs: number;
  volunteering: number;
  run_items?: CalendarItem[];
  volunteer_items?: CalendarItem[];
};

type StreakBreakdown = {
  total: number;
  runs: number;
  volunteering: number;
};

type ActivityCalendarHeatmapProps = {
  days: CalendarDay[];
  saturdayStreakMax: number;
  bestStreak?: StreakBreakdown;
  currentStreak?: StreakBreakdown;
};

type CellStatus = "run" | "vol" | "both" | "miss" | "off";

type WeekAggregate = {
  runs: number;
  volunteering: number;
  days: CalendarDay[];
};

const MAX_SATURDAYS_PER_YEAR = 53;

function parseIsoDateLocal(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) {
    return null;
  }
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

function toIsoDate(value: Date): string {
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${value.getFullYear()}-${month}-${day}`;
}

/** Saturday closing the Sunday–Saturday week that contains the given date. */
function weekSaturday(value: Date): Date {
  const result = new Date(value);
  result.setDate(result.getDate() + (6 - result.getDay()));
  return result;
}

function saturdaysOfYear(year: number): Date[] {
  const saturdays: Date[] = [];
  const current = new Date(year, 0, 1);
  current.setDate(current.getDate() + ((6 - current.getDay()) % 7));
  while (current.getFullYear() === year) {
    saturdays.push(new Date(current));
    current.setDate(current.getDate() + 7);
  }
  return saturdays;
}

function cellStatus(aggregate: WeekAggregate | undefined): CellStatus | null {
  if (!aggregate) {
    return null;
  }
  if (aggregate.runs > 0 && aggregate.volunteering > 0) {
    return "both";
  }
  if (aggregate.runs > 0) {
    return "run";
  }
  if (aggregate.volunteering > 0) {
    return "vol";
  }
  return null;
}

function formatDayMonth(iso: string): string {
  const match = /^\d{4}-(\d{2})-(\d{2})/.exec(iso);
  return match ? `${match[2]}.${match[1]}` : iso;
}

function itemLine(prefix: string, item: CalendarItem, dateSuffix: string): string {
  return `${prefix}: ${item.location} · ${platformCodeLabel(item.platform_code)}${dateSuffix}`;
}

function dayTooltipLines(day: CalendarDay): string[] {
  const parsed = parseIsoDateLocal(day.date);
  const suffix = parsed && parsed.getDay() !== 6 ? ` · ${formatDayMonth(day.date)}` : "";
  const lines: string[] = [];
  const runItems = day.run_items ?? [];
  const volunteerItems = day.volunteer_items ?? [];
  if (runItems.length > 0) {
    lines.push(...runItems.map((item) => itemLine("Пробежка", item, suffix)));
  } else if (day.runs > 0) {
    lines.push(`${pluralizeRu(day.runs, ["пробежка", "пробежки", "пробежек"])}${suffix}`);
  }
  if (volunteerItems.length > 0) {
    lines.push(...volunteerItems.map((item) => itemLine("Волонтёрство", item, suffix)));
  } else if (day.volunteering > 0) {
    lines.push(
      `${pluralizeRu(day.volunteering, ["волонтёрство", "волонтёрства", "волонтёрств"])}${suffix}`,
    );
  }
  return lines;
}

function cellTooltipLines(aggregate: WeekAggregate | undefined): string[] {
  if (!aggregate) {
    return ["Без активности"];
  }
  const sortedDays = [...aggregate.days].sort((a, b) => a.date.localeCompare(b.date));
  const lines = sortedDays.flatMap((day) => dayTooltipLines(day));
  return lines.length > 0 ? lines : ["Без активности"];
}

// total/runs/volunteering — три независимо посчитанных серии (любая
// активность, только пробежки, только волонтёрство), а не части одной общей
// серии — поэтому runs и volunteering не обязаны суммироваться в total.
function streakLine(label: string, streak: StreakBreakdown): string {
  return `${label} (пробежки и/или волонтёрство) — ${streak.total} ${saturdaysLabel(streak.total)} подряд`;
}

function streakBreakdownLine(label: string, streak: StreakBreakdown): string | null {
  if (streak.runs === 0 && streak.volunteering === 0) {
    return null;
  }
  return `${label} только пробежки — ${streak.runs}, только волонтёрства — ${streak.volunteering}`;
}

export function ActivityCalendarHeatmap({
  days,
  saturdayStreakMax,
  bestStreak,
  currentStreak,
}: ActivityCalendarHeatmapProps) {
  const byWeekSaturday = new Map<string, WeekAggregate>();
  let firstActivity: Date | null = null;

  for (const day of days) {
    const parsed = parseIsoDateLocal(day.date);
    if (!parsed || (day.runs <= 0 && day.volunteering <= 0)) {
      continue;
    }
    if (firstActivity === null || parsed < firstActivity) {
      firstActivity = parsed;
    }
    const saturday = weekSaturday(parsed);
    const key = toIsoDate(saturday);
    const aggregate = byWeekSaturday.get(key) ?? { runs: 0, volunteering: 0, days: [] };
    aggregate.runs += day.runs;
    aggregate.volunteering += day.volunteering;
    aggregate.days.push(day);
    byWeekSaturday.set(key, aggregate);
  }

  if (firstActivity === null) {
    return null;
  }
  const firstActivityDate = firstActivity;

  const today = new Date();
  const years: number[] = [];
  for (let year = today.getFullYear(); year >= firstActivityDate.getFullYear(); year -= 1) {
    years.push(year);
  }

  return (
    <div className="activity-cal">
      <div className="activity-cal-scroll">
        <div className="activity-cal-grid">
          {years.map((year) => {
            const saturdays = saturdaysOfYear(year);
            return (
              <div key={year} className="activity-cal-row">
                <span className="activity-cal-year">{year}</span>
                <div className="activity-cal-cells">
                  {saturdays.map((saturday) => {
                    const key = toIsoDate(saturday);
                    const aggregate = byWeekSaturday.get(key);
                    const activeStatus = cellStatus(aggregate);
                    const isOff =
                      activeStatus === null && (saturday > today || saturday < firstActivityDate);
                    const status: CellStatus = activeStatus ?? (isOff ? "off" : "miss");
                    const cell = (
                      <span
                        className={`activity-cal-cell activity-cal-cell-${status}`}
                        aria-hidden="true"
                      />
                    );
                    if (status === "off") {
                      return (
                        <span key={key} className="activity-cal-cell-wrap">
                          {cell}
                        </span>
                      );
                    }
                    return (
                      <span key={key} className="activity-cal-cell-wrap">
                        <ChartColumnTooltip
                          title={formatDate(key)}
                          lines={cellTooltipLines(aggregate)}
                        >
                          {cell}
                        </ChartColumnTooltip>
                      </span>
                    );
                  })}
                  {Array.from(
                    { length: MAX_SATURDAYS_PER_YEAR - saturdays.length },
                    (_, index) => (
                      <span key={`pad-${index}`} className="activity-cal-cell-wrap" />
                    ),
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
      <div className="analytics-chart-legend activity-cal-legend">
        <span className="analytics-legend-item">
          <span className="analytics-legend-swatch analytics-legend-swatch-run" />
          Пробежка
        </span>
        <span className="analytics-legend-item">
          <span className="analytics-legend-swatch analytics-legend-swatch-vol" />
          Волонтёрство
        </span>
        <span className="analytics-legend-item">
          <span className="analytics-legend-swatch activity-cal-swatch-both" />
          И то и другое
        </span>
        <span className="analytics-legend-item">
          <span className="analytics-legend-swatch activity-cal-swatch-miss" />
          Пропуск
        </span>
      </div>
      {bestStreak && bestStreak.total > 0 ? (
        <div className="muted analytics-chart-caption activity-cal-streaks">
          <p>{streakLine("Лучшая серия", bestStreak)}</p>
          {streakBreakdownLine("Лучшая серия", bestStreak) && <p>{streakBreakdownLine("Лучшая серия", bestStreak)}</p>}
          <p>{streakLine("Текущая серия", currentStreak ?? { total: 0, runs: 0, volunteering: 0 })}</p>
          {currentStreak && streakBreakdownLine("Текущая серия", currentStreak) && (
            <p>{streakBreakdownLine("Текущая серия", currentStreak)}</p>
          )}
        </div>
      ) : (
        saturdayStreakMax > 0 && (
          <p className="muted analytics-chart-caption">
            Лучшая серия — {saturdayStreakMax} {saturdaysLabel(saturdayStreakMax)} подряд
          </p>
        )
      )}
    </div>
  );
}
