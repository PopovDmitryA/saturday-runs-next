import { useEffect, useRef, useState } from "react";
import { ApiError, getCurrentUser, oauthStartUrl } from "../../lib/api";
import { PORTAL_ABOUT_PRIVACY_HREF } from "../../lib/portalRoutes";
import { PortalHeader } from "./PortalHeader";
import "./portal.css";

function readOAuthError(): string | null {
  return new URLSearchParams(window.location.search).get("oauth_error");
}

const RETURNING_KEY = "portalReturningUser";

function isReturningUser(): boolean {
  try {
    return window.localStorage.getItem(RETURNING_KEY) === "1";
  } catch {
    return false;
  }
}

function markReturningUser(): void {
  try {
    window.localStorage.setItem(RETURNING_KEY, "1");
  } catch {
    // localStorage может быть недоступен (приватный режим) — не критично
  }
}

const VALUE_POINTS = [
  {
    icon: (
      <svg viewBox="0 0 16 16">
        <polyline points="1.5,11.5 5.5,7 8.5,10 14.5,4" />
      </svg>
    ),
    title: "Все системы в одном месте",
    text: "5 вёрст, S95, parkrun и RunPark — единая история финишей и волонтёрств.",
  },
  {
    icon: (
      <svg viewBox="0 0 16 16">
        <polygon points="8,1.8 9.8,5.6 14,6.2 11,9.1 11.7,13.3 8,11.3 4.3,13.3 5,9.1 2,6.2 6.2,5.6" />
      </svg>
    ),
    title: "Рекорды и серии",
    text: "Личные рекорды, серии суббот и вехи вашей беговой истории.",
  },
  {
    icon: (
      <svg viewBox="0 0 16 16">
        <path d="M8 14.5C8 14.5 12.7 9.9 12.7 6.4 12.7 3.8 10.6 1.7 8 1.7 5.4 1.7 3.3 3.8 3.3 6.4 3.3 9.9 8 14.5 8 14.5Z" />
        <circle cx="8" cy="6.4" r="1.7" />
      </svg>
    ),
    title: "Карта визитов",
    text: "Где вы уже бегали и какие площадки ещё впереди.",
  },
] as const;

export function PortalLoginPage() {
  const consentRef = useRef<HTMLInputElement>(null);
  const [consentChecked, setConsentChecked] = useState(false);
  const [consentHint, setConsentHint] = useState(false);
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [oauthError, setOauthError] = useState<string | null>(() => readOAuthError());
  const [redirectingProvider, setRedirectingProvider] = useState<"vk" | "yandex" | null>(null);
  const [returning] = useState<boolean>(() => isReturningUser());

  useEffect(() => {
    const timeoutId = window.setTimeout(() => setCheckingAuth(false), 8_000);
    getCurrentUser()
      .then(() => {
        markReturningUser();
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

  const handleProviderClick = (
    event: React.MouseEvent<HTMLAnchorElement>,
    provider: "vk" | "yandex",
  ) => {
    if (!consentChecked) {
      event.preventDefault();
      setConsentHint(true);
      consentRef.current?.focus();
      consentRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    markReturningUser();
    setRedirectingProvider(provider);
  };

  if (checkingAuth) {
    return (
      <>
        <PortalHeader hideLogin />
        <main className="portal-home portal-login">
          <p className="portal-loading">Проверяем сессию…</p>
        </main>
      </>
    );
  }

  return (
    <>
      <PortalHeader hideLogin />
      <main className="portal-home portal-login">
        <div className={returning ? "portal-login-single" : "portal-login-split"}>
          {!returning && (
            <section className="portal-login-welcome" aria-label="Что даёт кабинет">
              <p className="portal-eyebrow">Личный кабинет</p>
              <h1>Найдите себя в статистике</h1>
              <p className="portal-hero-lead">
                Привяжите профили беговых систем один раз — дальше всё обновляется само.
              </p>
              <div className="portal-login-points">
                {VALUE_POINTS.map((point) => (
                  <div className="portal-login-point" key={point.title}>
                    <span className="portal-about-feature-icon">{point.icon}</span>
                    <div>
                      <b>{point.title}</b>
                      <p>{point.text}</p>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="portal-login-card" aria-label="Вход">
            <h2>{returning ? "С возвращением!" : "Вход на run5k.run"}</h2>
            <p className="portal-login-card-sub">
              {returning
                ? "Войдите привычным способом — и статистика на месте"
                : "Выберите удобный способ — пароль не нужен"}
            </p>

            {oauthError && (
              <div className="portal-login-error" role="alert">
                <p>Не удалось войти: {oauthError}</p>
                <button type="button" onClick={dismissOAuthError}>
                  Закрыть
                </button>
              </div>
            )}

            <label
              className={`portal-login-consent${
                consentHint && !consentChecked ? " portal-login-consent-highlight" : ""
              }`}
            >
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
                <a href={PORTAL_ABOUT_PRIVACY_HREF} target="_blank" rel="noreferrer">
                  условиями обработки данных
                </a>
              </span>
            </label>
            {consentHint && !consentChecked && (
              <p className="portal-login-consent-hint">
                Сначала отметьте согласие на обработку данных
              </p>
            )}

            <div className="portal-login-providers">
              <a
                href={oauthStartUrl("vk", "login", true)}
                className="portal-login-provider portal-login-provider-vk"
                data-full-nav
                onClick={(event) => handleProviderClick(event, "vk")}
              >
                <span className="portal-login-provider-logo" aria-hidden="true">
                  VK
                </span>
                {redirectingProvider === "vk" ? "Переход…" : "Войти через VK"}
              </a>
              <a
                href={oauthStartUrl("yandex", "login", true)}
                className="portal-login-provider portal-login-provider-yandex"
                data-full-nav
                onClick={(event) => handleProviderClick(event, "yandex")}
              >
                <span className="portal-login-provider-logo" aria-hidden="true">
                  Я
                </span>
                {redirectingProvider === "yandex" ? "Переход…" : "Войти через Яндекс"}
              </a>
            </div>

          </section>
        </div>
      </main>
    </>
  );
}
