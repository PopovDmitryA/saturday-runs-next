import type { GoalProgress } from "../../lib/api";

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

export function GoalCard({ goal, compact }: { goal: GoalProgress; compact?: boolean }) {
  return (
    <div className={`card goal-card${goal.done ? " goal-card-done" : ""}${compact ? " goal-card-compact" : ""}`}>
      <div className="goal-head">
        <span className="goal-icon" aria-hidden="true">
          {goal.icon}
        </span>
        <span className="goal-title">{goal.title}</span>
        <span className="goal-pct">{Math.round(goal.pct)}%</span>
      </div>
      <div className="goal-bar">
        <div
          className={`goal-bar-fill${goal.done ? " goal-bar-done" : ""}`}
          style={{ width: `${goal.pct}%` }}
        />
      </div>
      <div className="goal-meta">
        <span className="goal-progress-label">{progressLabel(goal)}</span>
        {!compact && forecastChip(goal)}
      </div>
    </div>
  );
}
