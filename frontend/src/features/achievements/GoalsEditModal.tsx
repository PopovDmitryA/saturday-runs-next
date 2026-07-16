import { useEffect, useMemo, useState } from "react";
import { DetailModal } from "../../components/DetailModal";
import { saveGoals, type GoalPreset, type GoalsResponse } from "../../lib/api";

function secondsToTimeInput(value: number): string {
  const minutes = Math.floor(value / 60);
  const seconds = value % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function timeInputToSeconds(value: string): number | null {
  const match = value.trim().match(/^(\d{1,2}):([0-5]\d)$/);
  if (!match) {
    return null;
  }
  return Number(match[1]) * 60 + Number(match[2]);
}

function formatTimeDigits(digits: string): string {
  if (digits.length <= 2) {
    return digits;
  }
  return `${digits.slice(0, -2)}:${digits.slice(-2)}`;
}

/** Живая маска ввода времени: только цифры, всегда MM:СС, секунды 00–59.
 * Последние две введённые цифры — всегда секунды (как в обычных time-пикерах);
 * если они дают >59, лишнюю цифру отбрасываем вместо показа неверного времени. */
function maskTimeInput(raw: string): string {
  let digits = raw.replace(/\D/g, "").slice(0, 4);
  while (digits.length >= 3 && Number(digits.slice(-2)) > 59) {
    digits = digits.slice(0, -1);
  }
  return formatTimeDigits(digits);
}

type DraftGoal = {
  selected: boolean;
  // Для kind=time храним строку «25:00», для остальных — число строкой
  raw: string;
};

/** «Сейчас: …» — текущий показатель пресета за год, виден даже до выбора цели,
 * чтобы адекватно выставить target. */
function presetCurrentLabel(preset: GoalPreset): string {
  if (preset.kind === "time") {
    return preset.current_display ? `Сейчас: ${preset.current_display}` : "Пока нет результатов в этом году";
  }
  if (preset.kind === "percent") {
    return `Сейчас: ${preset.current_display ?? "0%"}`;
  }
  const unitSuffix = preset.unit ? ` ${preset.unit}` : "";
  return `Сейчас: ${preset.current_value}${unitSuffix}`;
}

export function GoalsEditModal({
  open,
  goals,
  onClose,
  onSaved,
}: {
  open: boolean;
  goals: GoalsResponse;
  onClose: () => void;
  onSaved: (updated: GoalsResponse) => void;
}) {
  const [draft, setDraft] = useState<Record<string, DraftGoal>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    const next: Record<string, DraftGoal> = {};
    for (const preset of goals.presets) {
      const existing = goals.goals.find((goal) => goal.goal_type === preset.goal_type);
      const value = existing?.target_value ?? preset.default_target;
      next[preset.goal_type] = {
        selected: existing != null,
        raw: preset.kind === "time" ? secondsToTimeInput(value) : String(value),
      };
    }
    setDraft(next);
    setError(null);
  }, [open, goals]);

  const selectedCount = useMemo(
    () => Object.values(draft).filter((item) => item.selected).length,
    [draft],
  );

  const toggle = (preset: GoalPreset) => {
    setDraft((current) => {
      const item = current[preset.goal_type];
      if (!item) {
        return current;
      }
      if (!item.selected && selectedCount >= goals.max_goals) {
        return current;
      }
      return { ...current, [preset.goal_type]: { ...item, selected: !item.selected } };
    });
  };

  const setValue = (preset: GoalPreset, raw: string) => {
    setDraft((current) => ({
      ...current,
      [preset.goal_type]: { ...(current[preset.goal_type] ?? { selected: true, raw: "" }), raw },
    }));
  };

  const handleSave = async () => {
    const payload: Array<{ goal_type: string; target_value: number }> = [];
    for (const preset of goals.presets) {
      const item = draft[preset.goal_type];
      if (!item?.selected) {
        continue;
      }
      const value =
        preset.kind === "time" ? timeInputToSeconds(item.raw) : Number.parseInt(item.raw, 10);
      if (value == null || Number.isNaN(value)) {
        setError(
          preset.kind === "time"
            ? `«${preset.title}»: время в формате ММ:СС, например 25:00`
            : `«${preset.title}»: введи число`,
        );
        return;
      }
      if (value < preset.min || value > preset.max) {
        setError(
          preset.kind === "time"
            ? `«${preset.title}»: от ${secondsToTimeInput(preset.min)} до ${secondsToTimeInput(preset.max)}`
            : `«${preset.title}»: от ${preset.min} до ${preset.max}`,
        );
        return;
      }
      payload.push({ goal_type: preset.goal_type, target_value: value });
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await saveGoals(payload);
      onSaved(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить цели");
    } finally {
      setSaving(false);
    }
  };

  return (
    <DetailModal
      open={open}
      title={`Цели на ${goals.year} год`}
      onClose={onClose}
      footer={
        <div className="goal-modal-footer">
          {error && <p className="goal-modal-error">{error}</p>}
          <div className="goal-modal-actions">
            <span className="muted">
              Выбрано {selectedCount} из {goals.max_goals}
            </span>
            <button type="button" className="btn btn-ghost" onClick={onClose} disabled={saving}>
              Отмена
            </button>
            <button type="button" className="btn" onClick={() => void handleSave()} disabled={saving}>
              {saving ? "Сохраняю…" : "Сохранить"}
            </button>
          </div>
        </div>
      }
    >
      <p className="muted goal-modal-hint">
        До {goals.max_goals} целей на год. Прогресс считается автоматически по твоим результатам, а на главной
        появится компактный трекер.
      </p>
      <div className="goal-preset-list">
        {goals.presets.map((preset) => {
          const item = draft[preset.goal_type];
          const selected = item?.selected ?? false;
          const disabled = !selected && selectedCount >= goals.max_goals;
          return (
            <label
              key={preset.goal_type}
              className={`goal-preset${selected ? " goal-preset-selected" : ""}${disabled ? " goal-preset-disabled" : ""}`}
            >
              <input
                type="checkbox"
                checked={selected}
                disabled={disabled}
                onChange={() => toggle(preset)}
              />
              <span className="goal-preset-icon" aria-hidden="true">
                {preset.icon}
              </span>
              <span className="goal-preset-text">
                <span className="goal-preset-title">{preset.title}</span>
                <span className="goal-preset-description muted">{preset.description}</span>
                <span className="goal-preset-current muted">{presetCurrentLabel(preset)}</span>
              </span>
              <span className="goal-preset-input-wrap">
                <input
                  className="input goal-preset-input"
                  type={preset.kind === "time" ? "text" : "number"}
                  inputMode={preset.kind === "time" ? "numeric" : undefined}
                  min={preset.kind === "time" ? undefined : preset.min}
                  max={preset.kind === "time" ? undefined : preset.max}
                  placeholder={preset.kind === "time" ? "25:00" : String(preset.default_target)}
                  maxLength={preset.kind === "time" ? 5 : undefined}
                  value={item?.raw ?? ""}
                  disabled={!selected}
                  onClick={(event) => event.preventDefault()}
                  onChange={(event) =>
                    setValue(
                      preset,
                      preset.kind === "time" ? maskTimeInput(event.target.value) : event.target.value,
                    )
                  }
                />
                {preset.unit && <span className="goal-preset-unit muted">{preset.unit}</span>}
              </span>
            </label>
          );
        })}
      </div>
    </DetailModal>
  );
}
