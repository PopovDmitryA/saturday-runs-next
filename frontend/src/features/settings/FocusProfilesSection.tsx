import { useEffect, useState } from "react";
import {
  FOCUS_PROFILE_META,
  FOCUS_PROFILE_ORDER,
} from "../../components/FocusProfilesModal";
import {
  getDashboardFocus,
  updateDashboardFocus,
  type DashboardFocus,
  type FocusProfile,
} from "../../lib/api";

/**
 * Настройки → «Профили участника»: те же чекбоксы, что в модалке кабинета.
 * Здесь выбор можно поменять в любой момент; пустой выбор — показывать все
 * блоки. Рядом с профилями из автонабора — пометка «похоже на вас».
 */
export function FocusProfilesSection() {
  const [focus, setFocus] = useState<DashboardFocus | null>(null);
  const [checked, setChecked] = useState<Set<FocusProfile>>(new Set());
  const [saving, setSaving] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDashboardFocus()
      .then((data) => {
        if (!cancelled) {
          setFocus(data);
          setChecked(new Set(data.selected ?? []));
        }
      })
      .catch(() => {
        // Секция не критичная: при ошибке просто не показываем её.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!focus) {
    return null;
  }

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
    setSavedFlash(false);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const next = await updateDashboardFocus(
        FOCUS_PROFILE_ORDER.filter((profile) => checked.has(profile)),
        focus.suggested,
      );
      setFocus(next);
      setChecked(new Set(next.selected ?? []));
      setSavedFlash(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить выбор");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card focus-settings">
      <h2 className="section-title">Профили участника</h2>
      <p className="muted focus-settings-sub">
        Что вам интересно в субботних стартах: выбранные профили определяют, какие блоки
        статистики показывать в кабинете. Ничего не выбрано — показываются все.
      </p>
      <ul className="focus-modal-list">
        {FOCUS_PROFILE_ORDER.map((profile) => {
          const meta = FOCUS_PROFILE_META[profile];
          const suggested = focus.suggested.includes(profile);
          return (
            <li key={profile}>
              <label
                className={`focus-option${checked.has(profile) ? " focus-option-checked" : ""}`}
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
                    {suggested && <span className="focus-option-hint">похоже на вас</span>}
                  </span>
                  <span className="focus-option-desc">{meta.description}</span>
                </span>
              </label>
            </li>
          );
        })}
      </ul>
      {error && <p className="focus-modal-error">{error}</p>}
      <div className="focus-settings-actions">
        {savedFlash && <span className="focus-settings-saved">Сохранено</span>}
        <button type="button" className="btn" onClick={() => void save()} disabled={saving}>
          {saving ? "Сохраняем…" : "Сохранить"}
        </button>
      </div>
    </div>
  );
}
