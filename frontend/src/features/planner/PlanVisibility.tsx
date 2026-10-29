import { StatHintTooltip } from "../../components/StatHintTooltip";
import type { PlannerSettings } from "./plannerApi";
import "./planner.css";

/**
 * «Публично | Приватно» — у каждого плана свой выбор (решение Дмитрия
 * 27.10.2026: отдельного согласия и общей настройки нет, показ планов —
 * пункт общего согласия). По умолчанию переключатель стоит как профиль:
 * открыт — «Публично». Что именно видно, — одной фразой в «i», без описаний
 * в самом окне.
 *
 * Закрытый профиль — переключатель стоит на «Приватно» и не нажимается.
 * Ребёнку (до 15 лет по протоколам) выбрать можно, но имени не покажем:
 * публичный план на локации — число «ребёнок».
 */

type Forced = PlannerSettings["forced"];

const PLAN_HINT =
  "Публично — имя на странице локации и план в вашем профиле; приватно — видите только вы, на локации — только в общем числе.";
const REST_HINT = "Публично — «не бегает» в календаре вашего профиля; приватно — видите только вы.";
const MINOR_HINT =
  "По протоколам вам меньше 15 лет — имени не покажем: на странице локации публичный план будет числом «ребёнок».";
export const CLOSED_PROFILE_HINT = "Профиль закрыт — планы видите только вы.";

function hintText(forced: Forced, what: "plan" | "rest"): string {
  if (forced === "profile_private") {
    return CLOSED_PROFILE_HINT;
  }
  if (forced === "minor" && what === "plan") {
    return MINOR_HINT;
  }
  return what === "rest" ? REST_HINT : PLAN_HINT;
}

export function PlanVisibilityToggle({
  value,
  onChange,
  forced,
  disabled = false,
  what = "plan",
  compact = false,
}: {
  value: boolean;
  onChange: (next: boolean) => void;
  forced: Forced;
  disabled?: boolean;
  /** rest — отметка «Не бегаю»: она видна только в календаре профиля. */
  what?: "plan" | "rest";
  /** Маленький — в деталях дня «Моих планов». */
  compact?: boolean;
}) {
  const closed = forced === "profile_private";
  const on = closed ? false : value;
  const label = what === "rest" ? "Кто видит отметку «Не бегаю»" : "Кто видит план";
  return (
    <div className={compact ? "plan-vis plan-vis-compact" : "plan-vis"}>
      <div className="plan-mode-seg plan-vis-seg" role="group" aria-label={label}>
        <button type="button" aria-pressed={on} disabled={disabled || closed} onClick={() => !on && onChange(true)}>
          Публично
        </button>
        <button type="button" aria-pressed={!on} disabled={disabled || closed} onClick={() => on && onChange(false)}>
          Приватно
        </button>
      </div>
      <StatHintTooltip text={hintText(forced, what)} label={label} className="plan-people-hint plan-vis-hint">
        <span className="loc-stat-info" aria-hidden="true">
          i
        </span>
      </StatHintTooltip>
    </div>
  );
}
