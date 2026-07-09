import { useCallback, useEffect, useRef, useState } from "react";
import {
  checkProfileSlug,
  getProfileSlug,
  updateProfileSlug,
  type ProfileSlugSettings,
} from "../../lib/api";

type CheckState =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "available"; normalized: string }
  | { kind: "unavailable"; reason: string };

// Онлайн-проверка занятости с задержкой, чтобы не дёргать бэкенд на каждую букву.
const CHECK_DEBOUNCE_MS = 400;

export function ProfileLinkSection() {
  const [settings, setSettings] = useState<ProfileSlugSettings | null>(null);
  const [value, setValue] = useState("");
  const [check, setCheck] = useState<CheckState>({ kind: "idle" });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const debounceRef = useRef<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getProfileSlug();
      setSettings(data);
      setValue(data.slug ?? "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить настройку ссылки");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const currentSlug = settings?.slug ?? null;
  const trimmed = value.trim().toLowerCase();
  const unchanged = trimmed === (currentSlug ?? "");

  // Debounced-проверка доступности при вводе.
  useEffect(() => {
    if (debounceRef.current) {
      window.clearTimeout(debounceRef.current);
    }
    if (trimmed === "" || unchanged) {
      setCheck({ kind: "idle" });
      return;
    }
    setCheck({ kind: "checking" });
    debounceRef.current = window.setTimeout(() => {
      let cancelled = false;
      checkProfileSlug(trimmed)
        .then((res) => {
          if (cancelled) return;
          setCheck(
            res.available
              ? { kind: "available", normalized: res.normalized }
              : { kind: "unavailable", reason: res.reason ?? "Недоступно" },
          );
        })
        .catch(() => {
          if (!cancelled) setCheck({ kind: "idle" });
        });
      return () => {
        cancelled = true;
      };
    }, CHECK_DEBOUNCE_MS);
    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
    };
  }, [trimmed, unchanged]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const data = await updateProfileSlug(trimmed || null);
      setSettings(data);
      setValue(data.slug ?? "");
      setCheck({ kind: "idle" });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить ссылку");
    } finally {
      setSaving(false);
    }
  };

  const handleClear = async () => {
    setSaving(true);
    setError(null);
    try {
      const data = await updateProfileSlug(null);
      setSettings(data);
      setValue("");
      setCheck({ kind: "idle" });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сбросить ссылку");
    } finally {
      setSaving(false);
    }
  };

  const canSave = !saving && !unchanged && trimmed !== "" && check.kind === "available";

  return (
    <section className="card">
      <h2 className="section-title">Ссылка на профиль</h2>
      <p className="muted settings-lead">
        Задайте короткую ссылку на свой публичный профиль вместо адреса с номером. Только латинские
        буквы, цифры, дефис и подчёркивание{settings ? `, от ${settings.min_length} до ${settings.max_length} символов` : ""}.
        Ссылку можно поменять в любой момент.
      </p>

      {loading && <p className="muted">Загрузка…</p>}
      {error && <p className="error-text">{error}</p>}

      {!loading && settings && (
        <>
          {settings.public_url && settings.slug && (
            <p className="settings-lead profile-slug-current">
              Текущая ссылка:{" "}
              <a href={`/users/${settings.slug}`} target="_blank" rel="noreferrer">
                {settings.public_url}
              </a>
            </p>
          )}

          <div className="profile-slug-field">
            <span className="profile-slug-prefix">/users/</span>
            <input
              type="text"
              className="profile-slug-input"
              value={value}
              maxLength={settings.max_length}
              placeholder="my-nickname"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              disabled={saving}
              onChange={(e) => setValue(e.target.value)}
            />
          </div>

          <div className="profile-slug-status" aria-live="polite">
            {check.kind === "checking" && <span className="muted">Проверяем…</span>}
            {check.kind === "available" && (
              <span className="profile-slug-ok">✓ Ссылка свободна</span>
            )}
            {check.kind === "unavailable" && (
              <span className="error-text">{check.reason}</span>
            )}
          </div>

          <div className="actions-row">
            <button type="button" className="btn primary" disabled={!canSave} onClick={() => void handleSave()}>
              {saving ? "Сохранение…" : "Сохранить ссылку"}
            </button>
            {currentSlug && (
              <button
                type="button"
                className="btn secondary"
                disabled={saving}
                onClick={() => void handleClear()}
              >
                Сбросить
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}
