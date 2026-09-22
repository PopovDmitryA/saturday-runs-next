import { useEffect, useState } from "react";
import {
  setHistoryMilestoneDisabledKinds,
  type HistoryMilestoneKindSetting,
  type HistoryMilestoneSettings,
  type MyHistoryMilestoneKind,
} from "../../lib/api";
// Оформление шторки общее с «Какие роли считать» в рейтингах (классы vrm-*).
import "../leaderboards/leaderboards.css";

const KIND_ICON: Record<MyHistoryMilestoneKind, string> = {
  first_run: "🏁",
  first_run_platform: "🚩",
  run_club: "🏅",
  run_club_platform: "🎖️",
  location_club: "🎯",
  volunteer_location_club: "📍",
  global_pr: "🏆",
  pr: "⚡",
  location_pr: "🥉",
  location_course_record: "👑",
  location_age_group_record: "🏵️",
  first_foreign_parkrun: "✈️",
  first_foreign_run: "✈️",
  new_country: "🌍",
  new_region: "🧭",
  new_city: "🏙️",
  new_location: "🗺️",
  first_volunteer: "🤝",
  volunteer_club: "🤝",
  volunteer_club_platform: "🙌",
  saturday_streak: "🔥",
  saturday_run_streak: "🏃",
  saturday_volunteer_streak: "🙋",
};

// Группы для раскладки списка — плоский список из двух десятков видов читается тяжело.
const GROUPS: { title: string; kinds: MyHistoryMilestoneKind[] }[] = [
  {
    title: "Пробежки",
    kinds: ["first_run", "first_run_platform", "run_club", "run_club_platform", "location_club"],
  },
  {
    title: "Рекорды",
    kinds: ["location_course_record", "location_age_group_record", "global_pr", "pr", "location_pr"],
  },
  {
    title: "География",
    kinds: [
      "first_foreign_parkrun",
      "first_foreign_run",
      "new_country",
      "new_region",
      "new_city",
      "new_location",
    ],
  },
  {
    title: "Волонтёрство",
    kinds: ["first_volunteer", "volunteer_club", "volunteer_club_platform", "volunteer_location_club"],
  },
  {
    title: "Серия суббот",
    kinds: ["saturday_streak", "saturday_run_streak", "saturday_volunteer_streak"],
  },
];

type HistoryMilestonesModalProps = {
  settings: HistoryMilestoneSettings;
  onSaved: (settings: HistoryMilestoneSettings) => void;
  onClose: () => void;
};

export function HistoryMilestonesModal({ settings, onSaved, onClose }: HistoryMilestonesModalProps) {
  const kinds = settings.kinds;
  const [draft, setDraft] = useState<Set<string>>(
    () => new Set(kinds.filter((item) => item.enabled).map((item) => item.kind)),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const byKind = new Map(kinds.map((item) => [item.kind, item]));

  const toggle = (kind: string, checked: boolean) => {
    setDraft((current) => {
      const next = new Set(current);
      if (checked) {
        next.add(kind);
      } else {
        next.delete(kind);
      }
      return next;
    });
  };

  const apply = async () => {
    setSaving(true);
    setError(null);
    try {
      const disabled = kinds.filter((item) => !draft.has(item.kind)).map((item) => item.kind);
      onSaved(await setHistoryMilestoneDisabledKinds(disabled));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить");
      setSaving(false);
    }
  };

  return (
    <div className="vrm-backdrop" role="presentation" onClick={onClose}>
      <div
        className="vrm-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Какие вехи показывать"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="vrm-head">
          <h2>Какие вехи показывать</h2>
          <button type="button" className="vrm-close" aria-label="Закрыть" onClick={onClose}>
            ×
          </button>
        </header>

        <p className="vrm-intro muted">{settings.description}</p>

        <div className="vrm-groups">
          {GROUPS.map((group) => {
            const rows = group.kinds
              .map((kind) => byKind.get(kind))
              .filter((item): item is HistoryMilestoneKindSetting => Boolean(item));
            if (rows.length === 0) {
              return null;
            }
            return (
              <section key={group.title} className="vrm-group">
                <header className="vrm-group-head">
                  <span className="vrm-group-title">{group.title}</span>
                </header>
                <ul className="vrm-role-list hms-list">
                  {rows.map((item) => (
                    <li key={item.kind}>
                      <label className="vrm-role hms-row">
                        <input
                          type="checkbox"
                          checked={draft.has(item.kind)}
                          onChange={(event) => toggle(item.kind, event.target.checked)}
                        />
                        <span className="hms-icon" aria-hidden="true">
                          {KIND_ICON[item.kind] ?? "⭐"}
                        </span>
                        <span className="hms-body">
                          <span className="hms-label">{item.label}</span>
                          <span className="hms-description">{item.description}</span>
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>

        <div className="vrm-bulk">
          <button
            type="button"
            className="vrm-bulk-btn"
            onClick={() => setDraft(new Set(kinds.map((item) => item.kind)))}
          >
            Выделить все
          </button>
          <button type="button" className="vrm-bulk-btn" onClick={() => setDraft(new Set())}>
            Снять все
          </button>
        </div>

        {error && <p className="error-text">{error}</p>}

        <footer className="vrm-foot">
          <span className="muted vrm-count">
            Показано видов: {draft.size} из {kinds.length}
          </span>
          <div className="vrm-actions">
            <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
              Отмена
            </button>
            <button type="button" className="btn btn-sm" disabled={saving} onClick={() => void apply()}>
              {saving ? "Сохраняем…" : "Применить"}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
