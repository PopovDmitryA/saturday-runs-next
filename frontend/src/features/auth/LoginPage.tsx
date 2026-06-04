import { useCallback, useEffect, useState } from "react";
import { SiteHeader } from "../../components/SiteHeader";
import {
  createLoginRequest,
  getCurrentUser,
  getLoginRequestStatus,
  oauthStartUrl,
} from "../../lib/api";
import { SITE_PUBLIC_HOME_HREF } from "../../lib/siteBrand";
import { PUBLIC_NAV_ITEMS } from "../../lib/siteNav";

type LoginState = "idle" | "loading" | "waiting" | "link_sent" | "expired" | "error";

export function LoginPage() {
  const [state, setState] = useState<LoginState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [botUrl, setBotUrl] = useState<string | null>(null);
  const [requestToken, setRequestToken] = useState<string | null>(null);
  const [consentChecked, setConsentChecked] = useState(false);

  useEffect(() => {
    getCurrentUser()
      .then(() => {
        window.location.href = "/dashboard";
      })
      .catch(() => {
        // not logged in
      });
  }, []);

  const startTelegramLogin = useCallback(async () => {
    setState("loading");
    setError(null);
    try {
      const data = await createLoginRequest(false);
      setBotUrl(data.bot_url);
      setRequestToken(data.request_token);
      setState("waiting");
      window.open(data.bot_url, "_blank", "noopener,noreferrer");
    } catch (err) {
      setState("error");
      setError(err instanceof Error ? err.message : "Unknown error");
    }
  }, []);

  const startOAuthLogin = (provider: "vk" | "yandex") => {
    if (!consentChecked) {
      return;
    }
    window.location.href = oauthStartUrl(provider, "login", true);
  };

  useEffect(() => {
    if (state !== "waiting" || !requestToken) {
      return;
    }

    const interval = window.setInterval(async () => {
      try {
        const status = await getLoginRequestStatus(requestToken);
        if (status.status === "link_sent") {
          setState("link_sent");
        } else if (status.status === "expired") {
          setState("expired");
        }
      } catch {
        // ignore polling errors
      }
    }, 2000);

    return () => window.clearInterval(interval);
  }, [state, requestToken]);

  return (
    <>
      <SiteHeader
        homeHref={SITE_PUBLIC_HOME_HREF}
        navItems={PUBLIC_NAV_ITEMS}
        actions={
          <a className="btn btn-ghost btn-sm" href={SITE_PUBLIC_HOME_HREF}>
            На главную
          </a>
        }
      />
      <main className="app shell-content login-page">
        <h1 className="login-page-title">Вход в личный кабинет</h1>
        <p className="subtitle">Telegram, VK или Яндекс — выберите удобный способ</p>
        <p className="muted login-intro-link">
          <a href="/demo">Демо без входа</a>
        </p>

        {state === "idle" && (
          <>
            <label className="login-consent-field">
              <input
                type="checkbox"
                checked={consentChecked}
                onChange={(event) => setConsentChecked(event.target.checked)}
              />
              <span>
                Я ознакомился(ась) с{" "}
                <a href="/about#privacy" target="_blank" rel="noreferrer">
                  условиями обработки данных
                </a>
                {". "}
                Для Telegram согласие также подтверждается в боте.
              </span>
            </label>

            <div className="login-provider-buttons">
              <button
                type="button"
                className="btn primary login-provider-btn"
                onClick={startTelegramLogin}
                disabled={!consentChecked}
              >
                Войти через Telegram
              </button>
              <button
                type="button"
                className="btn secondary login-provider-btn login-provider-btn-vk"
                onClick={() => startOAuthLogin("vk")}
                disabled={!consentChecked}
              >
                Войти через VK
              </button>
              <button
                type="button"
                className="btn secondary login-provider-btn login-provider-btn-yandex"
                onClick={() => startOAuthLogin("yandex")}
                disabled={!consentChecked}
              >
                Войти через Яндекс
              </button>
            </div>
          </>
        )}

        {state === "loading" && <p className="muted">Подготовка входа…</p>}

        {state === "waiting" && (
          <div className="card">
            <p>Откройте Telegram-бота и подтвердите вход.</p>
            <p className="muted login-consent-hint">
              В боте нужно нажать «Принимаю» — это фиксирует согласие на обработку персональных данных.
            </p>
            {botUrl && (
              <a className="btn secondary" href={botUrl} target="_blank" rel="noreferrer">
                Открыть бота
              </a>
            )}
            <p className="muted">Ожидаем подтверждение…</p>
          </div>
        )}

        {state === "link_sent" && (
          <div className="card success">
            <p>Ссылка для входа отправлена в Telegram.</p>
            <p className="muted">Нажмите её, чтобы завершить авторизацию.</p>
          </div>
        )}

        {state === "expired" && (
          <div className="card error">
            <p>Время ожидания истекло.</p>
            <button type="button" className="btn primary" onClick={() => setState("idle")}>
              Попробовать снова
            </button>
          </div>
        )}

        {state === "error" && (
          <div className="card error">
            <p>{error}</p>
            <button type="button" className="btn primary" onClick={() => setState("idle")}>
              Попробовать снова
            </button>
          </div>
        )}

        <p className="login-about-link">
          <a href="/demo">Посмотреть демо</a>
          {" · "}
          <a href="/about">О проекте</a>
        </p>
      </main>
    </>
  );
}
