import type { ReactNode } from "react";
import { ActivityCalendarHeatmap } from "../../../components/ActivityCalendarHeatmap";
import { FinishTimeDistribution } from "../../../components/FinishTimeDistribution";
import { PaceTrendChart } from "../../../components/DashboardAnalytics";
import { LastSaturdayCard } from "../../../components/LastSaturdayCard";
import type { DashboardResponse } from "../../../lib/api";
import { formatDuration, formatPace, pluralFormRu } from "../../../lib/format";
import {
  PORTAL_CABINET_MAP_HREF,
  PORTAL_CABINET_RUNS_HREF,
  PORTAL_CABINET_VOLUNTEERING_HREF,
} from "../../../lib/portalRoutes";

/**
 * Bento-сетка кабинета: размер плитки говорит о важности. Свежее событие
 * занимает целый блок, ключевые цифры — маленькие ячейки, графики живут
 * в сетке наравне с числами, а не сваливаются в подвал страницы.
 *
 * Размеры задаются классами tile-{lg,md,sm}; сетка сама сжимается с
 * четырёх колонок до двух на планшете и до одной на телефоне.
 */
function Tile({
  size = "sm",
  title,
  href,
  children,
  className = "",
}: {
  size?: "lg" | "md" | "sm";
  title?: string;
  href?: string;
  children: ReactNode;
  className?: string;
}) {
  const classes = `cab-tile cab-tile-${size} ${className}`.trim();
  const body = (
    <>
      {title && <h2 className="cab-tile-title">{title}</h2>}
      {children}
    </>
  );
  return href ? (
    <a className={`${classes} cab-tile-link`} href={href}>
      {body}
    </a>
  ) : (
    <section className={classes}>{body}</section>
  );
}

/** Крупная цифра с подписью — базовая ячейка сетки. */
function StatTile({
  value,
  label,
  note,
  href,
  accent,
}: {
  value: ReactNode;
  label: string;
  note?: string;
  href?: string;
  accent?: "runs" | "volunteering" | "speed" | "places";
}) {
  return (
    <Tile size="sm" href={href} className={accent ? `cab-tile-${accent}` : ""}>
      <span className="cab-tile-value">{value}</span>
      <span className="cab-tile-label">{label}</span>
      {note && <span className="cab-tile-note">{note}</span>}
    </Tile>
  );
}

export function CabinetBentoGrid({
  data,
  slots,
}: {
  data: DashboardResponse;
  /** Существующие блоки кабинета, встроенные в сетку как плитки. */
  slots: { goals?: ReactNode; onThisDay?: ReactNode; history?: ReactNode; rating?: ReactNode };
}) {
  const stats = data.stats;
  const analytics = stats?.analytics;
  const totalRuns = stats?.total_runs ?? 0;
  const totalVol = stats?.total_volunteering ?? 0;
  const calendar = analytics?.activity_calendar ?? [];
  const finishTimes = analytics?.finish_times_sec ?? [];

  return (
    <div className="cab-bento">
      {analytics?.last_saturday && (
        <Tile size="lg" className="cab-tile-event">
          <LastSaturdayCard data={analytics.last_saturday} own bare />
        </Tile>
      )}

      <StatTile
        value={totalRuns}
        label={pluralFormRu(totalRuns, ["пробежка", "пробежки", "пробежек"])}
        note={
          analytics?.runs_last_12_months ? `${analytics.runs_last_12_months} за 12 мес.` : undefined
        }
        href={PORTAL_CABINET_RUNS_HREF}
        accent="runs"
      />
      <StatTile
        value={totalVol}
        label={pluralFormRu(totalVol, ["волонтёрство", "волонтёрства", "волонтёрств"])}
        note={
          analytics?.volunteering_index ? `индекс ${analytics.volunteering_index}` : undefined
        }
        href={PORTAL_CABINET_VOLUNTEERING_HREF}
        accent="volunteering"
      />
      {analytics?.best_finish_time_sec != null && (
        <StatTile
          value={formatDuration(analytics.best_finish_time_sec).replace(/^00:/, "")}
          label="лучшее время"
          note={
            analytics.avg_finish_time_sec != null
              ? `в среднем ${formatDuration(analytics.avg_finish_time_sec).replace(/^00:/, "")}`
              : undefined
          }
          accent="speed"
        />
      )}
      <StatTile
        value={analytics?.unique_run_locations ?? 0}
        label={pluralFormRu(analytics?.unique_run_locations ?? 0, [
          "локация",
          "локации",
          "локаций",
        ])}
        note={
          analytics?.avg_pace_sec_per_km != null
            ? `темп ${formatPace(analytics.avg_pace_sec_per_km)} /км`
            : undefined
        }
        href={PORTAL_CABINET_MAP_HREF}
        accent="places"
      />

      {slots.onThisDay && <Tile size="md" className="cab-tile-plain">{slots.onThisDay}</Tile>}
      {slots.goals && <Tile size="md" className="cab-tile-plain">{slots.goals}</Tile>}

      {calendar.length > 0 && (
        <Tile size="lg" title="Календарь суббот">
          <ActivityCalendarHeatmap
            days={calendar}
            saturdayStreakMax={analytics?.saturday_streak_max ?? 0}
            bestStreak={{
              total: analytics?.saturday_streak_max ?? 0,
              runs: analytics?.saturday_run_streak_max ?? 0,
              volunteering: analytics?.saturday_vol_streak_max ?? 0,
            }}
            currentStreak={{
              total: analytics?.saturday_streak_current ?? 0,
              runs: analytics?.saturday_run_streak_current ?? 0,
              volunteering: analytics?.saturday_vol_streak_current ?? 0,
            }}
          />
        </Tile>
      )}

      {(analytics?.pace_trend?.length ?? 0) > 1 && (
        <Tile size="md" title="Динамика темпа">
          <PaceTrendChart
            monthly={analytics!.pace_trend}
            yearly={analytics!.pace_trend_yearly ?? []}
          />
        </Tile>
      )}

      {finishTimes.length >= 3 && (
        <Tile size="md" title="Распределение результатов">
          <FinishTimeDistribution times={finishTimes} />
        </Tile>
      )}

      {slots.history && <Tile size="md" className="cab-tile-plain">{slots.history}</Tile>}
      {slots.rating && <Tile size="md" className="cab-tile-plain">{slots.rating}</Tile>}
    </div>
  );
}
