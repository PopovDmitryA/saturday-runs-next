import { useMemo } from "react";
import type { GoalProgress, User } from "../../lib/api";
import { ShareRowButton } from "../sharing/ShareRowButton";
import { goalSubject } from "../sharing/subjects";
import type { ShareEntryPoint } from "../sharing/types";

function forecastChip(goal: GoalProgress) {
  if (goal.done) {
    return <span className="goal-chip goal-chip-done">Цель выполнена 🎉</span>;
  }
  if (goal.kind === "count" && goal.forecast_value != null && goal.on_track != null) {
    return goal.on_track ? (
      <span className="goal-chip goal-chip-ok">Прогноз: ~{goal.forecast_value} — успеваешь ✓</span>
    ) : (
      <span className="goal-chip goal-chip-warn">Прогноз: ~{goal.forecast_value} — нужно поднажать</span>
    );
  }
  if (goal.kind === "streak" && goal.on_track != null) {
    return goal.on_track ? (
      <span className="goal-chip goal-chip-ok">Ещё достижима в этом году ✓</span>
    ) : (
      <span className="goal-chip goal-chip-warn">В этом году суббот уже не хватит</span>
    );
  }
  if (goal.kind === "percent" && goal.on_track != null) {
    return goal.on_track ? (
      <span className="goal-chip goal-chip-ok">Ещё достижимо в этом году ✓</span>
    ) : (
      <span className="goal-chip goal-chip-warn">Суббот до конца года уже не хватит</span>
    );
  }
  return null;
}

function recentDeltaLabel(goal: GoalProgress): string {
  return goal.kind === "time" ? `+${goal.recent_delta}с быстрее` : `+${goal.recent_delta}`;
}

function RecentDeltaBadge({ goal }: { goal: GoalProgress }) {
  if (!goal.recent_delta) {
    return null;
  }
  return (
    <span className="recent-delta-badge" title="Продвинула последняя пробежка">
      ↑ {recentDeltaLabel(goal)}
    </span>
  );
}

function progressLabel(goal: GoalProgress): string {
  if (goal.kind === "time") {
    if (!goal.current_display) {
      return `Пока нет результатов в этом году · цель ${goal.target_display ?? ""}`;
    }
    return `Лучшее в этом году: ${goal.current_display} · цель ${goal.target_display}`;
  }
  if (goal.kind === "percent") {
    return `Активен в ${goal.current_display ?? "0%"} суббот · цель ${goal.target_display ?? ""}`;
  }
  return `${goal.current_value} из ${goal.target_value} ${goal.unit}`.trim();
}

export function GoalCard({
  goal,
  compact,
  user,
  shareEntry = "goals",
}: {
  goal: GoalProgress;
  compact?: boolean;
  /** Нужен постеру: имя на карточке. undefined — сессия ещё проверяется. */
  user?: User | null;
  shareEntry?: ShareEntryPoint;
}) {
  // Постер собирается заранее: невыполненная цель уходит в сториз прогрессом
  // («31 / 50» + полоса), выполненная — победой.
  const subject = useMemo(() => goalSubject(goal, user ?? null), [goal, user]);
  return (
    <div className={`card goal-card${goal.done ? " goal-card-done" : ""}${compact ? " goal-card-compact" : ""}`}>
      {/* Название занимает всю ширину карточки: в узкой колонке тизера оно
          иначе делит строку с процентом и рвётся посреди слова. Процент и
          «Поделиться» встали к полосе — процент там ещё и подписывает её. */}
      <div className="goal-head">
        <span className="goal-icon" aria-hidden="true">
          {goal.icon}
        </span>
        <span className="goal-title">{goal.title}</span>
      </div>
      <div className="goal-progress-row">
        <div className="goal-bar">
          <div
            className={`goal-bar-fill${goal.done ? " goal-bar-done" : ""}`}
            style={{ width: `${goal.pct}%` }}
          />
        </div>
        <span className="goal-pct">{Math.round(goal.pct)}%</span>
        <ShareRowButton subject={subject} entry={shareEntry} />
      </div>
      <div className="goal-meta">
        {/* Бейдж-дельта стоит в строке прогресса, а не в шапке: в шапке он
            отнимал у названия последние миллиметры и лез на процент. */}
        <span className="goal-progress-label">
          {progressLabel(goal)} <RecentDeltaBadge goal={goal} />
        </span>
        {!compact && forecastChip(goal)}
      </div>
    </div>
  );
}
