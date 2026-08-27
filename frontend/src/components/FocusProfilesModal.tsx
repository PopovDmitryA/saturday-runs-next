import { useState } from "react";
import { updateDashboardFocus, type DashboardFocus, type FocusProfile } from "../lib/api";

export const FOCUS_PROFILE_META: Record<
  FocusProfile,
  { icon: string; title: string; description: string }
> = {
  regular: {
    icon: "📅",
    title: "Завсегдатай",
    description: "Суббота — это ритуал: серии, регулярность, календарь активности.",
  },
  racer: {
    icon: "⚡",
    title: "Скоростник",
    description: "Бегу на результат: время, рекорды, места в протоколе.",
  },
  tourist: {
    icon: "🧭",
    title: "Паркран-турист",
    description: "Коллекционирую локации: новые парки, города и регионы.",
  },
  volunteer: {
    icon: "🤝",
    title: "Волонтёр",
    description: "Помогаю проводить старты: смены, роли, вклад в организацию.",
  },
};

export const FOCUS_PROFILE_ORDER: FocusProfile[] = ["regular", "racer", "tourist", "volunteer"];

/**
 * Модалка «профили участника». Два сценария:
 * — первичный (selected === null): «на основании активности мы выбрали…»,
 *   предотмечен автонабор (пустой автонабор — отмечаем все: новичка
 *   угадывать не из чего, пусть снимет лишнее);
 * — дополнение (newly_suggested не пуст): после привязки нового аккаунта
 *   добавились профили — они предотмечены и подсвечены.
 */
export function FocusProfilesModal({
  focus,
  onSaved,
  onDismiss,
}: {
  focus: DashboardFocus;
  onSaved: (next: DashboardFocus) => void;
  onDismiss: () => void;
}) {
  const isFirstRun = focus.selected === null;
  const initial = isFirstRun
    ? focus.suggested.length > 0
      ? focus.suggested
      : FOCUS_PROFILE_ORDER
    : [...(focus.selected ?? []), ...focus.newly_suggested];
  const [checked, setChecked] = useState<Set<FocusProfile>>(new Set(initial));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = (profile: FocusProfile) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(profile)) {
        next.delete(profile);
      } else {
        next.add(profile);
      }
      return next;
    });
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const next = await updateDashboardFocus(
        FOCUS_PROFILE_ORDER.filter((profile) => checked.has(profile)),
        focus.suggested,
      );
      onSaved(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить выбор");
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="Профили участника">
      <div className="modal focus-modal">
        <h2 className="focus-modal-title">
          {isFirstRun ? "Мы присмотрелись к вашей статистике" : "Ваш профиль дополнен"}
        </h2>
        <p className="focus-modal-sub">
          {isFirstRun
            ? "На основании активности мы подобрали вам профили участника. Они определяют, какие блоки статистики показывать в кабинете, — поправьте, если не угадали."
            : "По данным привязанного аккаунта мы добавили новый профиль — он подсвечен. Снимите отметку, если это не про вас."}
        </p>
        <ul className="focus-modal-list">
          {FOCUS_PROFILE_ORDER.map((profile) => {
            const meta = FOCUS_PROFILE_META[profile];
            const isNew = focus.newly_suggested.includes(profile);
            return (
              <li key={profile}>
                <label
                  className={`focus-option${checked.has(profile) ? " focus-option-checked" : ""}${
                    isNew ? " focus-option-new" : ""
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={checked.has(profile)}
                    onChange={() => toggle(profile)}
                  />
                  <span className="focus-option-icon" aria-hidden="true">
                    {meta.icon}
                  </span>
                  <span className="focus-option-text">
                    <span className="focus-option-title">
                      {meta.title}
                      {isNew && <span className="focus-option-badge">новый</span>}
                    </span>
                    <span className="focus-option-desc">{meta.description}</span>
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
        <p className="focus-modal-note">
          Выбор ничего не удаляет: скрытые блоки вернутся, как только вы включите профиль в
          настройках.
        </p>
        {error && <p className="focus-modal-error">{error}</p>}
        <div className="focus-modal-actions">
          <button type="button" className="btn secondary" onClick={onDismiss} disabled={saving}>
            Позже
          </button>
          <button type="button" className="btn" onClick={() => void save()} disabled={saving}>
            {saving ? "Сохраняем…" : "Сохранить"}
          </button>
        </div>
      </div>
    </div>
  );
}
