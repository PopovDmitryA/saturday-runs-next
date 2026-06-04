import { useEffect, useState } from "react";
import { SiteHeader } from "../../components/SiteHeader";
import { getCurrentUser, oauthStartUrl } from "../../lib/api";
import { SITE_PUBLIC_HOME_HREF } from "../../lib/siteBrand";
import { PUBLIC_NAV_ITEMS } from "../../lib/siteNav";

export function LoginPage() {
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

  const startOAuthLogin = (provider: "vk" | "yandex") => {
    if (!consentChecked) {
      return;
    }
    window.location.href = oauthStartUrl(provider, "login", true);
  };

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
            .
          </span>
        </label>

        <div className="login-provider-buttons">
          <button
            type="button"
            className="btn primary login-provider-btn login-provider-btn-vk"
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

        <p className="login-about-link">
          <a href="/demo">Посмотреть демо</a>
          {" · "}
          <a href="/about">О проекте</a>
        </p>
      </main>
    </>
  );
}
