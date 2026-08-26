import { useCallback, useEffect, useState } from "react";
import { getNotificationSettings, updateNotificationSettings } from "../../lib/api";

export function NotificationSettingsSection() {
  const [enabled, setEnabled] = useState(false);
  const [description, setDescription] = useState("");
  const [email, setEmail] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const settings = await getNotificationSettings();
      setEnabled(settings.enabled);
      setDescription(settings.description);
      setEmail(settings.email);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить настройки рассылки");
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
      const settings = await updateNotificationSettings(nextEnabled);
      setEnabled(settings.enabled);
      setEmail(settings.email);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить настройки рассылки");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="card">
      <h2 className="section-title">Новости проекта</h2>
      {loading && <p className="muted">Загрузка…</p>}
      {error && <p className="error-text">{error}</p>}

      {!loading && (
        <>
          <p className="muted settings-lead">{description}</p>
          <div className="settings-platform-row privacy-settings-row">
            <div className="settings-platform-info">
              <span className="settings-platform-name">
                {email ? `Письма на ${email}` : "Письма о новостях проекта"}
              </span>
              <span className={`settings-toggle-label ${enabled ? "on" : "off"}`}>
                {enabled ? "Подписаны" : "Не подписаны"}
              </span>
            </div>
            <label className="toggle-switch" aria-label="Новости проекта на почту">
              <input
                type="checkbox"
                className="toggle-switch-input"
                checked={enabled}
                // Без привязанной почты слать некуда: сначала «Способы входа».
                disabled={saving || (!email && !enabled)}
                onChange={(event) => void handleToggle(event.target.checked)}
              />
              <span className="toggle-switch-track">
                <span className="toggle-switch-thumb" />
              </span>
            </label>
          </div>
          {!email && (
            <p className="muted settings-platform-hint">
              Чтобы получать письма, добавьте почту в разделе «Способы входа» ниже.
            </p>
          )}
        </>
      )}
    </section>
  );
}
