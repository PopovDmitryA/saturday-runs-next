import { useCallback, useEffect, useState } from "react";
import { getPrivacySettings, updatePrivacySettings } from "../../lib/api";

export function PrivacySettingsSection() {
  const [enabled, setEnabled] = useState(false);
  const [description, setDescription] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const settings = await getPrivacySettings();
      setEnabled(settings.enabled);
      setDescription(settings.description);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить настройки приватности");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleToggle = async (nextEnabled: boolean) => {
    setSaving(true);
    setError(null);
    try {
      const settings = await updatePrivacySettings(nextEnabled);
      setEnabled(settings.enabled);
      setDescription(settings.description);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить настройки приватности");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="card">
      <h2 className="section-title">Приватный профиль</h2>
      {loading && <p className="muted">Загрузка…</p>}
      {error && <p className="error-text">{error}</p>}

      {!loading && (
        <>
          <p className="muted settings-lead">{description}</p>
          <div className="settings-platform-row privacy-settings-row">
            <div className="settings-platform-info">
              <span className="settings-platform-name">Скрыть профиль от других участников</span>
              <span className={`settings-toggle-label ${enabled ? "on" : "off"}`}>
                {enabled ? "Включено" : "Выключено"}
              </span>
            </div>
            <label className="toggle-switch" aria-label="Приватный профиль">
              <input
                type="checkbox"
                className="toggle-switch-input"
                checked={enabled}
                disabled={saving}
                onChange={(event) => void handleToggle(event.target.checked)}
              />
              <span className="toggle-switch-track">
                <span className="toggle-switch-thumb" />
              </span>
            </label>
          </div>
        </>
      )}
    </section>
  );
}
