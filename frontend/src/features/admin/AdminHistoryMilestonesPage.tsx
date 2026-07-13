import { useCallback, useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import {
  getAdminHistoryMilestones,
  setAdminHistoryMilestoneEnabled,
  type HistoryMilestoneKindSetting,
  type MyHistoryMilestoneKind,
} from "../../lib/api";
import { AdminSubnav } from "./AdminSubnav";

const KIND_ICON: Record<MyHistoryMilestoneKind, string> = {
  first_run: "🏁",
  first_run_platform: "🚩",
  run_club: "🏅",
  run_club_platform: "🎖️",
  location_club: "🎯",
  volunteer_location_club: "📍",
  global_pr: "🏆",
  pr: "⚡",
  first_foreign_parkrun: "✈️",
  first_foreign_run: "✈️",
  new_country: "🌍",
  new_region: "🧭",
  new_city: "🏙️",
  new_location: "🗺️",
  first_volunteer: "🤝",
  volunteer_club: "🤝",
  volunteer_club_platform: "🙌",
};

function AdminHistoryMilestonesContent() {
  const [kinds, setKinds] = useState<HistoryMilestoneKindSetting[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // kind'ы, у которых сейчас в полёте запрос на переключение — блокируем чекбокс.
  const [pending, setPending] = useState<Set<string>>(new Set());

  const load = useCallback(() => {
    setError(null);
    getAdminHistoryMilestones()
      .then((result) => setKinds(result.kinds))
      .catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить вехи"));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = async (kind: MyHistoryMilestoneKind, nextEnabled: boolean) => {
    setPending((prev) => new Set(prev).add(kind));
    setError(null);
    // Оптимистичное обновление — админ сразу видит переключённый чекбокс.
    setKinds((prev) =>
      prev ? prev.map((item) => (item.kind === kind ? { ...item, enabled: nextEnabled } : item)) : prev,
    );
    try {
      await setAdminHistoryMilestoneEnabled(kind, nextEnabled);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить");
      // Откат при ошибке — перезагружаем актуальное состояние с сервера.
      load();
    } finally {
      setPending((prev) => {
        const next = new Set(prev);
        next.delete(kind);
        return next;
      });
    }
  };

  const enabledCount = kinds?.filter((item) => item.enabled).length ?? 0;

  return (
    <AppShell title="Вехи истории" activePath="/admin">
      <AdminSubnav activePath="/admin/history-milestones" />

      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      <section className="card admin-milestones-section">
        <h2 className="section-title">Виды вех «Моя история»</h2>
        <p className="muted admin-milestones-lead">
          Все виды вех, которые могут попасть в таймлайн /history пользователя. Выключенный вид
          сразу перестаёт показываться всем — используйте, если нужно оперативно убрать веху.
          {kinds && (
            <>
              {" "}
              Включено: {enabledCount} из {kinds.length}.
            </>
          )}
        </p>
        <ul className="admin-milestones-list">
          {kinds?.map((item) => (
            <li key={item.kind} className={`admin-milestone-row ${item.enabled ? "" : "disabled"}`}>
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
                  <span className="admin-milestone-kind">{item.kind}</span>
                  <span className="admin-milestone-description">{item.description}</span>
                </span>
              </label>
            </li>
          ))}
          {kinds && kinds.length === 0 && <li className="muted">Видов вех пока нет</li>}
        </ul>
      </section>
    </AppShell>
  );
}

export function AdminHistoryMilestonesPage() {
  return <RequireAdmin>{() => <AdminHistoryMilestonesContent />}</RequireAdmin>;
}
