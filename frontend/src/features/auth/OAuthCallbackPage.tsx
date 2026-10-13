import { useEffect, useState } from "react";
import { finishOAuthLogin } from "../../lib/api";
import { forgetLoginNext, loginRetryHref } from "../../lib/loginReturn";
import { clearCachedUser } from "../../lib/useOptionalUser";

type OAuthCallbackPageProps = {
  provider: "vk" | "yandex";
};

export function OAuthCallbackPage({ provider }: OAuthCallbackPageProps) {
  const [message, setMessage] = useState("Завершаем вход…");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    // Провайдер вернул сюда без нашего ?next= — со входа человек попробует
    // ещё раз, и вернуться должен на ту же страницу: адрес берём из запаса,
    // который оставила страница входа этой вкладки (lib/loginReturn).
    const oauthError = params.get("error_description") || params.get("error");
    if (oauthError) {
      window.location.replace(loginRetryHref({ oauth_error: oauthError }));
      return;
    }

    const code = params.get("code");
    const state = params.get("state");
    if (!code || !state) {
      window.location.replace(loginRetryHref({ oauth_error: "OAuth callback is missing code or state." }));
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
        // Куда вести, решил сервер (страница возврата лежала в state OAuth).
        forgetLoginNext();
        const target = result.redirect.startsWith("/") ? result.redirect : `/${result.redirect}`;
        window.location.replace(target);
      })
      .catch((err: unknown) => {
        const text = err instanceof Error ? err.message : "Не удалось завершить вход";
        setMessage(text);
        window.setTimeout(() => {
          window.location.replace(loginRetryHref({ oauth_error: text }));
        }, 2500);
      });
  }, [provider]);

  return (
    <main className="app">
      <p className="muted">{message}</p>
    </main>
  );
}
