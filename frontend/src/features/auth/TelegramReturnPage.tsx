import { useEffect, useState } from "react";
import { loginWithTelegramWidget } from "../../lib/api";

// Telegram возвращает человека с данными во фрагменте адреса
// (#tgAuthResult=<base64 от JSON>). Фрагмент браузер сервер не отправляет,
// поэтому разобрать ответ может только страница — она же передаёт данные на
// бэкенд, где проверяется подпись.
function readAuthResult(): Record<string, string> | null {
  const hash = window.location.hash.replace(/^#/, "");
  const encoded = new URLSearchParams(hash).get("tgAuthResult");
  if (!encoded) {
    return null;
  }
  try {
    // base64url: Telegram шлёт его без выравнивания.
    const normalized = encoded.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
    const json = decodeURIComponent(escape(window.atob(padded)));
    const parsed = JSON.parse(json) as Record<string, unknown>;
    const data: Record<string, string> = {};
    for (const [key, value] of Object.entries(parsed)) {
      if (value !== null && value !== undefined) {
        data[key] = String(value);
      }
    }
    return data;
  } catch {
    return null;
  }
}

export function TelegramReturnPage() {
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const state = new URLSearchParams(window.location.search).get("state") ?? "";
    const data = readAuthResult();
    if (!data) {
      setError("Telegram не передал данные для входа. Попробуйте ещё раз.");
      return;
    }
    void loginWithTelegramWidget(data, state)
      .then((result) => {
        // Привязка к существующему профилю может упереться в чужой аккаунт с
        // тем же телеграмом — тогда несём токен в настройки, где человек решит,
        // объединять ли профили.
        const suffix = result.merge_token ? `?merge_token=${result.merge_token}` : "";
        window.location.replace(`/${result.redirect}${suffix}`);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Не удалось войти через Telegram");
      });
  }, []);

  return (
    <main className="portal-home portal-login">
      {error ? (
        <div className="portal-login-card" role="alert">
          <h1 className="portal-login-card-title">Вход не завершён</h1>
          <p>{error}</p>
          <a className="btn primary" href="/login">
            Вернуться ко входу
          </a>
        </div>
      ) : (
        <p className="portal-loading">Завершаем вход через Telegram…</p>
      )}
    </main>
  );
}
