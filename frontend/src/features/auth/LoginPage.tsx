import { useEffect, useRef, useState } from "react";
import { SiteHeader } from "../../components/SiteHeader";
import { ApiError, getCurrentUser, oauthStartUrl } from "../../lib/api";
import { SITE_PUBLIC_HOME_HREF } from "../../lib/siteBrand";
import { PUBLIC_NAV_ITEMS } from "../../lib/siteNav";

function readOAuthError(): string | null {
  return new URLSearchParams(window.location.search).get("oauth_error");
}

export function LoginPage() {
  const consentRef = useRef<HTMLInputElement>(null);
  const [consentChecked, setConsentChecked] = useState(false);
  const [consentHint, setConsentHint] = useState(false);
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [oauthError, setOauthError] = useState<string | null>(() => readOAuthError());
  const [redirectingProvider, setRedirectingProvider] = useState<"vk" | "yandex" | null>(null);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => setCheckingAuth(false), 8_000);
    getCurrentUser()
      .then(() => {
        window.location.href = "/dashboard";
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) {
          return;
        }
      })
      .finally(() => {
        window.clearTimeout(timeoutId);
        setCheckingAuth(false);
      });
  }, []);

  const dismissOAuthError = () => {
    setOauthError(null);
    const url = new URL(window.location.href);
    url.searchParams.delete("oauth_error");
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  };

  const handleProviderClick = (event: React.MouseEvent<HTMLAnchorElement>, provider: "vk" | "yandex") => {
    if (!consentChecked) {
      event.preventDefault();
      setConsentHint(true);
      consentRef.current?.focus();
      consentRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    setRedirectingProvider(provider);
  };

  if (checkingAuth) {
    return (
      <main className="app">
        <p className="muted">Проверяем сессию…</p>
      </main>
    );
  }

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
        <p className="subtitle">VK или Яндекс — выберите удобный способ</p>
        <p className="muted login-intro-link">
          <a href="/demo">Демо без входа</a>
        </p>

        {oauthError && (
          <div className="card error login-oauth-error">
            <p>Не удалось войти: {oauthError}</p>
            <button type="button" className="btn btn-ghost btn-sm" onClick={dismissOAuthError}>
              Закрыть
            </button>
          </div>
        )}

        <label className={`login-consent-field${consentHint && !consentChecked ? " login-consent-field-highlight" : ""}`}>
          <input
            ref={consentRef}
            type="checkbox"
            checked={consentChecked}
            onChange={(event) => {
              setConsentChecked(event.target.checked);
              if (event.target.checked) {
                setConsentHint(false);
              }
            }}
          />
          <span>
            Я ознакомился(ась) с{" "}
            <a href="/about#privacy" target="_blank" rel="noreferrer">
              условиями обработки данных
            </a>
            .
          </span>
        </label>

        {consentHint && !consentChecked && (
          <p className="login-consent-hint card error login-oauth-error">
            Сначала отметьте галочку согласия на обработку данных.
          </p>
        )}

        <div className="login-provider-buttons">
          <a
            href={oauthStartUrl("vk", "login", true)}
            className="btn primary login-provider-btn login-provider-btn-vk"
            data-full-nav
            onClick={(event) => handleProviderClick(event, "vk")}
          >
            {redirectingProvider === "vk" ? "Переход…" : "Войти через VK"}
          </a>
          <a
            href={oauthStartUrl("yandex", "login", true)}
            className="btn secondary login-provider-btn login-provider-btn-yandex"
            data-full-nav
            onClick={(event) => handleProviderClick(event, "yandex")}
          >
            {redirectingProvider === "yandex" ? "Переход…" : "Войти через Яндекс"}
          </a>
        </div>

        <p className="login-about-link">
          <a href="/demo">Посмотреть демо</a>
          {" · "}
          <a href="/about">О проекте</a>
        </p>
      </main>
    </>
  );
}
