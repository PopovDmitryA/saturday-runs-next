import { useEffect, useState } from "react";
import { getGoals, type GoalsResponse } from "../../lib/api";
import { GoalCard } from "../achievements/GoalCard";

/** Компактный трекер целей на главной: только прогресс + кнопка настройки,
 * вся работа с целями — на странице «Достижения». */
export function GoalsTeaser() {
  const [goals, setGoals] = useState<GoalsResponse | null>(null);

  useEffect(() => {
    getGoals()
      .then(setGoals)
      .catch(() => setGoals(null));
  }, []);

  if (goals == null) {
    return null;
  }

  if (goals.goals.length === 0) {
    return (
      <div className="card goals-teaser goals-teaser-empty">
        <div className="goals-teaser-head">
          <h2 className="section-title">Цели на {goals.year} год</h2>
        </div>
        <p className="muted">
          Поставь цель — пробежки, новые локации или время — и следи за прогрессом прямо здесь.
        </p>
        <a className="btn secondary btn-sm" href="/achievements">
          Выбрать цели →
        </a>
      </div>
    );
  }

  return (
    <div className="card goals-teaser">
      <div className="goals-teaser-head">
        <h2 className="section-title">Цели на {goals.year} год</h2>
        <a
          className="btn btn-ghost btn-sm goals-teaser-settings"
          href="/achievements"
          title="Настроить цели"
          aria-label="Настроить цели"
        >
          ⚙ Настроить
        </a>
      </div>
      <div className="goals-teaser-grid">
        {goals.goals.map((goal) => (
          <GoalCard key={goal.goal_type} goal={goal} compact />
        ))}
      </div>
    </div>
  );
}
