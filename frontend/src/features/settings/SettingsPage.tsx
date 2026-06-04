import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { AuthProvidersSection } from "./AuthProvidersSection";
import { RequireAuth } from "../../components/RequireAuth";
import {
  getAutoSyncSettings,
  updateAutoSyncSettings,
  type AutoSyncSettings,
} from "../../lib/api";
import { formatDateTime, platformCodeLabel } from "../../lib/format";

function SettingsContent() {
  const mergeToken = useMemo(
    () => new URLSearchParams(window.location.search).get("merge_token"),
    [],
  );
  const [data, setData] = useState<AutoSyncSettings | null>(null);
  const [draft, setDraft] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await getAutoSyncSettings();
      setData(response);
      const next: Record<string, boolean> = {};
      for (const item of response.platforms) {
        next[item.platform_code] =
          item.platform_code === "parkrun" ? false : item.enabled;
      }
      setDraft(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить настройки");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleToggle = (platformCode: string, enabled: boolean) => {
    if (platformCode === "parkrun") {
      return;
    }
    setDraft((prev) => ({ ...prev, [platformCode]: enabled }));
    setSuccess(null);
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const payload = { ...draft, parkrun: false };
      const updated = await updateAutoSyncSettings(payload);
      setData(updated);
      setSuccess("Настройки сохранены");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить настройки");
    } finally {
      setSaving(false);
    }
  };

  const hasChanges =
    data !== null &&
    data.platforms.some(
      (item) =>
        item.platform_code !== "parkrun" &&
        draft[item.platform_code] !== item.enabled,
    );

  return (
    <AppShell title="Настройки" activePath="/settings">
      <AuthProvidersSection initialMergeToken={mergeToken} />

      {loading && <p className="muted">Загрузка…</p>}

      {error && (
        <div className="card error">
          <p>{error}</p>
          <button type="button" className="btn secondary" onClick={() => void load()}>
            Повторить
          </button>
        </div>
      )}

      {!loading && !error && data && (
        <>
          <section className="card">
            <h2 className="section-title">Автообновление при входе</h2>
            <p className="muted settings-lead">
              По умолчанию автоматическая синхронизация выключена для всех систем. Если включить
              автообновление для системы из списка ниже, данные обновятся не чаще одного раза в{" "}
              {data.interval_hours} ч при открытии раздела «Главная». Ручное обновление — по клику на
              время справа в строке каждой привязанной системы (не чаще раза в 30 минут на систему).
            </p>
            {data.last_login_auto_sync_at && (
              <p className="muted">
                Последнее автообновление: {formatDateTime(String(data.last_login_auto_sync_at))}
              </p>
            )}

            <ul className="settings-platform-list">
              {data.platforms.map((item) => {
                const isParkrun = item.platform_code === "parkrun";
                const enabled = isParkrun ? false : (draft[item.platform_code] ?? false);
                return (
                  <li key={item.platform_code} className="settings-platform-row">
                    <div className="settings-platform-info">
                      <span className="settings-platform-name">
                        {platformCodeLabel(item.platform_code)}
                      </span>
                      {isParkrun ? (
                        <span className="muted settings-platform-hint">
                          Автообновление недоступно. Профиль parkrun обновляется только по вашему
                          запросу — нажимайте кнопку обновления у привязанного профиля, если на
                          parkrun появились новые пробежки, которых ещё нет на этом сайте.
                        </span>
                      ) : (
                        !item.linked && (
                          <span className="muted settings-platform-hint">Профиль не привязан</span>
                        )
                      )}
                      <span className={`settings-toggle-label ${enabled ? "on" : "off"}`}>
                        {enabled ? "Вкл" : "Выкл"}
                      </span>
                    </div>
                    <label
                      className="toggle-switch"
                      title={
                        isParkrun
                          ? "Автообновление parkrun недоступно"
                          : enabled
                            ? "Выключить"
                            : "Включить"
                      }
                    >
                      <input
                        type="checkbox"
                        className="toggle-switch-input"
                        checked={enabled}
                        disabled={isParkrun}
                        onChange={(event) =>
                          handleToggle(item.platform_code, event.target.checked)
                        }
                      />
                      <span className="toggle-switch-track" aria-hidden="true">
                        <span className="toggle-switch-thumb" />
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>

            <div className="actions-row">
              <button
                type="button"
                className="btn primary"
                disabled={saving || !hasChanges}
                onClick={() => void handleSave()}
              >
                {saving ? "Сохранение…" : "Сохранить"}
              </button>
            </div>

            {success && <p className="form-success">{success}</p>}
          </section>
        </>
      )}
    </AppShell>
  );
}

export function SettingsPage() {
  return <RequireAuth>{() => <SettingsContent />}</RequireAuth>;
}
