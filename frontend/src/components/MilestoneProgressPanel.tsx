import type { MilestoneProgress } from "../lib/api";
import { pluralizeRu } from "../lib/format";

const GOAL_LABELS: Record<MilestoneProgress["kind"], (target: number) => string> = {
  runs: (target) => `Клуб ${target} пробежек`,
  volunteering: (target) => `Клуб ${target} волонтёрств`,
  locations: (target) => `${target} площадок в коллекции`,
};

const REMAINING_FORMS: Record<MilestoneProgress["kind"], readonly [string, string, string]> = {
  runs: ["пробежка", "пробежки", "пробежек"],
  volunteering: ["волонтёрство", "волонтёрства", "волонтёрств"],
  locations: ["площадка", "площадки", "площадок"],
};

/**
 * «Ближайшие цели»: прогресс-бар к каждой невзятой ступени вместо голого
 * «осталось N». Цель с видимым путём мотивирует сильнее, чем факт
 * (паттерн приложения parkrun и бейджей Smashrun).
 */
export function MilestoneProgressPanel({ items }: { items: MilestoneProgress[] }) {
  if (items.length === 0) {
    return null;
  }

  return (
    <div className="card milestone-panel">
      <h2 className="section-title">Ближайшие цели</h2>
      <ul className="milestone-list">
        {items.map((item) => (
          <li key={item.kind} className="milestone-row">
            <div className="milestone-row-head">
              <span className="milestone-goal">{GOAL_LABELS[item.kind](item.target)}</span>
              <span className="milestone-remaining">
                осталось {pluralizeRu(item.remaining, REMAINING_FORMS[item.kind])}
              </span>
            </div>
            <div
              className="milestone-bar"
              role="progressbar"
              aria-valuenow={Math.round(item.percent)}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={GOAL_LABELS[item.kind](item.target)}
            >
              <span className="milestone-bar-fill" style={{ width: `${item.percent}%` }} />
            </div>
            <span className="milestone-scale">
              {item.current} из {item.target}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
