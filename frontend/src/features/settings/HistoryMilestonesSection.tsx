import { useCallback, useEffect, useState } from "react";
import {
  getHistoryMilestoneSettings,
  setHistoryMilestoneEnabled,
  type HistoryMilestoneKindSetting,
  type MyHistoryMilestoneKind,
} from "../../lib/api";

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

export function HistoryMilestonesSection() {
  const [kinds, setKinds] = useState<HistoryMilestoneKindSetting[] | null>(null);
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  // kind'ы, у которых сейчас в полёте запрос — блокируем чекбокс.
  const [pending, setPending] = useState<Set<string>>(new Set());

  const load = useCallback(() => {
    setError(null);
    getHistoryMilestoneSettings()
      .then((result) => {
        setKinds(result.kinds);
        setDescription(result.description);
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Не удалось загрузить настройки вех"),
      );
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = async (kind: MyHistoryMilestoneKind, nextEnabled: boolean) => {
    setPending((prev) => new Set(prev).add(kind));
    setError(null);
    // Оптимистичное обновление — галочка переключается сразу.
    setKinds((prev) =>
      prev ? prev.map((item) => (item.kind === kind ? { ...item, enabled: nextEnabled } : item)) : prev,
    );
    try {
      const result = await setHistoryMilestoneEnabled(kind, nextEnabled);
      setKinds(result.kinds);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить");
      load(); // откат к серверному состоянию
    } finally {
      setPending((prev) => {
        const next = new Set(prev);
        next.delete(kind);
        return next;
      });
    }
  };

  const byKind = new Map((kinds ?? []).map((item) => [item.kind, item]));
  const enabledCount = kinds?.filter((item) => item.enabled).length ?? 0;

  return (
    <details className="card history-ms-spoiler">
      <summary className="history-ms-summary">
        <span className="history-ms-summary-main">
          <span className="history-ms-summary-chevron" aria-hidden="true">
            ›
          </span>
          <span className="history-ms-summary-title">Типы вех в «Моей истории»</span>
        </span>
        {kinds && (
          <span className="history-ms-summary-count">
            показано {enabledCount} из {kinds.length}
          </span>
        )}
      </summary>

      {error && <p className="error-text">{error}</p>}
      {!kinds && !error && <p className="muted">Загрузка…</p>}

      {kinds && (
        <>
          <p className="muted settings-lead history-ms-lead">{description}</p>
          {GROUPS.map((group) => {
            const rows = group.kinds
              .map((kind) => byKind.get(kind))
              .filter((item): item is HistoryMilestoneKindSetting => Boolean(item));
            if (rows.length === 0) return null;
            return (
              <div className="history-ms-group" key={group.title}>
                <h3 className="history-ms-group-title">{group.title}</h3>
                <ul className="admin-milestones-list">
                  {rows.map((item) => (
                    <li
                      key={item.kind}
                      className={`admin-milestone-row ${item.enabled ? "" : "disabled"}`}
                    >
                      <label className="admin-milestone-toggle">
                        <input
                          type="checkbox"
                          checked={item.enabled}
                          disabled={pending.has(item.kind)}
                          onChange={(event) => void toggle(item.kind, event.target.checked)}
                        />
                        <span className="admin-milestone-icon" aria-hidden="true">
                          {KIND_ICON[item.kind] ?? "⭐"}
                        </span>
                        <span className="admin-milestone-body">
                          <span className="admin-milestone-label">{item.label}</span>
                          <span className="admin-milestone-description">{item.description}</span>
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </>
      )}
    </details>
  );
}
