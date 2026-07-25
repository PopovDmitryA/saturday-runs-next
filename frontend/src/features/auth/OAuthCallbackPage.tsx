import { useEffect, useState } from "react";
import { finishOAuthLogin } from "../../lib/api";
import { clearCachedUser } from "../../lib/useOptionalUser";

type OAuthCallbackPageProps = {
  provider: "vk" | "yandex";
};

export function OAuthCallbackPage({ provider }: OAuthCallbackPageProps) {
  const [message, setMessage] = useState("Завершаем вход…");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const oauthError = params.get("error_description") || params.get("error");
    if (oauthError) {
      window.location.replace(`/login?oauth_error=${encodeURIComponent(oauthError)}`);
      return;
    }

    const code = params.get("code");
    const state = params.get("state");
    if (!code || !state) {
      window.location.replace("/login?oauth_error=OAuth%20callback%20is%20missing%20code%20or%20state.");
      return;
    }

    void finishOAuthLogin(provider, {
      code,
      state,
      device_id: params.get("device_id"),
      payload: params.get("payload"),
    })
      .then((result) => {
        // Кэш сессии помнит «аноним» с прошлой страницы — если его не сбросить,
        // следующая страница отправит только что вошедшего обратно на вход.
        clearCachedUser();
        const target = result.redirect.startsWith("/") ? result.redirect : `/${result.redirect}`;
        window.location.replace(target);
      })
      .catch((err: unknown) => {
        const text = err instanceof Error ? err.message : "Не удалось завершить вход";
        setMessage(text);
        window.setTimeout(() => {
          window.location.replace(`/login?oauth_error=${encodeURIComponent(text)}`);
        }, 2500);
      });
  }, [provider]);

  return (
    <main className="app">
      <p className="muted">{message}</p>
    </main>
  );
}
