import { useEffect, useRef, useState } from "react";
import { trackAuthStart } from "../../lib/abTest";
import {
  ApiError,
  getCurrentUser,
  oauthStartUrl,
  requestEmailCode,
  verifyEmailCode,
} from "../../lib/api";
import { PORTAL_ABOUT_PRIVACY_HREF } from "../../lib/portalRoutes";
import { PortalFooter } from "./PortalFooter";
import { PortalHeader } from "./PortalHeader";
import "./portal.css";

function readOAuthError(): string | null {
  return new URLSearchParams(window.location.search).get("oauth_error");
}

const RETURNING_KEY = "portalReturningUser";
// Куку sr_known сервер ставит после каждого успешного входа (см. auth.py).
// Её наличие означает: человек уже принимал условия обработки данных, и его
// согласие лежит в профиле — переспрашивать не за чем.
const KNOWN_DEVICE_COOKIE = "sr_known";

function hasLoggedInBefore(): boolean {
  try {
    return document.cookie.split("; ").some((item) => item.startsWith(`${KNOWN_DEVICE_COOKIE}=`));
  } catch {
    return false;
  }
}
// Адрес, на который только что ушёл код. Нужен, чтобы обновление страницы или
// переход в почту и обратно не выбрасывали человека на первый шаг: код у него
// на руках, а запросить новый мешает лимит на ящик.
const PENDING_EMAIL_KEY = "portalPendingLoginEmail";
const PENDING_EMAIL_TTL_MS = 10 * 60 * 1000;

type PendingEmail = { email: string; sentAt: number };

function readPendingEmail(): PendingEmail | null {
  try {
    const raw = window.sessionStorage.getItem(PENDING_EMAIL_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as PendingEmail;
    if (!parsed?.email || Date.now() - parsed.sentAt > PENDING_EMAIL_TTL_MS) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function rememberPendingEmail(email: string): void {
  try {
    window.sessionStorage.setItem(
      PENDING_EMAIL_KEY,
      JSON.stringify({ email, sentAt: Date.now() }),
    );
  } catch {
    // sessionStorage может быть недоступен (приватный режим) — не критично
  }
}

function forgetPendingEmail(): void {
  try {
    window.sessionStorage.removeItem(PENDING_EMAIL_KEY);
  } catch {
    // см. выше
  }
}

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

// Фирменные марки провайдеров: VK ID — белые буквы на #0077FF, Яндекс ID — белая «Я» на #FC3F1D.
function VkMark() {
  return (
    <svg className="portal-login-scope-mark" viewBox="0 0 24 24" aria-hidden="true">
      <rect width="24" height="24" rx="7" fill="#0077ff" />
      <text
        x="12"
        y="16.2"
        textAnchor="middle"
        fontSize="10"
        fontWeight="800"
        letterSpacing="0.3"
        fill="#ffffff"
      >
        VK
      </text>
    </svg>
  );
}

function YandexMark() {
  return (
    <svg className="portal-login-scope-mark" viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="12" fill="#fc3f1d" />
      <text x="12" y="17" textAnchor="middle" fontSize="14" fontWeight="700" fill="#ffffff">
        Я
      </text>
    </svg>
  );
}

// Вход по коду: своей марки у почты нет, рисуем нейтральный конверт.
function MailMark() {
  return (
    <svg className="portal-login-scope-mark" viewBox="0 0 24 24" aria-hidden="true">
      <rect width="24" height="24" rx="7" fill="#5b6472" />
      <path
        d="M5.5 8.5h13v7h-13z"
        fill="none"
        stroke="#ffffff"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path d="M5.5 9l6.5 4.5L18.5 9" fill="none" stroke="#ffffff" strokeWidth="1.6" />
    </svg>
  );
}

export function PortalLoginPage() {
  const consentRef = useRef<HTMLInputElement>(null);
  const [knownDevice] = useState<boolean>(() => hasLoggedInBefore());
  const [consentChecked, setConsentChecked] = useState(() => hasLoggedInBefore());
  // Рассылка — отдельное согласие, по умолчанию выключена: молчаливой подписки
  // быть не должно.
  const [newsConsent, setNewsConsent] = useState(false);
  const [consentHint, setConsentHint] = useState(false);
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [oauthError, setOauthError] = useState<string | null>(() => readOAuthError());
  const [redirectingProvider, setRedirectingProvider] = useState<"vk" | "yandex" | null>(null);
  const [returning] = useState<boolean>(() => isReturningUser());
  // Вход по почте: сначала адрес, потом код из письма. Второй шаг показываем
  // только после отправки — пустое поле кода на первом экране лишь путает.
  const [emailStep, setEmailStep] = useState<"idle" | "code">(() =>
    readPendingEmail() ? "code" : "idle",
  );
  const [emailOpen, setEmailOpen] = useState<boolean>(() => readPendingEmail() !== null);
  const [email, setEmail] = useState(() => readPendingEmail()?.email ?? "");
  const [emailCode, setEmailCode] = useState("");
  const [emailBusy, setEmailBusy] = useState(false);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [emailNotice, setEmailNotice] = useState<string | null>(() => {
    const pending = readPendingEmail();
    return pending ? `Код отправлен на ${pending.email}. Он действует 10 минут.` : null;
  });

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
    // Ступень воронки: человек прошёл галочку согласия и уходит к провайдеру.
    // Клики, остановленные проверкой выше, сюда не попадают — разрыв между
    // cta_click и auth_start и есть цена экрана входа.
    trackAuthStart(provider);
    setRedirectingProvider(provider);
  };

  const requireConsent = (): boolean => {
    if (consentChecked) {
      return true;
    }
    setConsentHint(true);
    consentRef.current?.focus();
    consentRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    return false;
  };

  const handleEmailSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!requireConsent()) {
      return;
    }
    setEmailError(null);
    setEmailBusy(true);
    try {
      await requestEmailCode(email.trim(), true, newsConsent);
      markReturningUser();
      rememberPendingEmail(email.trim());
      setEmailStep("code");
      setEmailNotice(`Код отправлен на ${email.trim()}. Он действует 10 минут.`);
    } catch (err) {
      setEmailError(err instanceof Error ? err.message : "Не удалось отправить код");
    } finally {
      setEmailBusy(false);
    }
  };

  const handleCodeSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setEmailError(null);
    setEmailBusy(true);
    try {
      const result = await verifyEmailCode(email.trim(), emailCode.trim());
      forgetPendingEmail();
      window.location.href = `/${result.redirect}`;
    } catch (err) {
      setEmailError(err instanceof Error ? err.message : "Не удалось войти");
      setEmailBusy(false);
    }
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
              <button
                type="button"
                className="portal-login-provider portal-login-provider-email"
                aria-expanded={emailOpen}
                aria-controls="portal-login-email-form"
                onClick={() => setEmailOpen((open) => !open)}
              >
                <span className="portal-login-provider-logo" aria-hidden="true">
                  <svg viewBox="0 0 24 24">
                    <path
                      d="M4 7h16v10H4z"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.8"
                      strokeLinejoin="round"
                    />
                    <path d="M4 7.5l8 5.5 8-5.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
                  </svg>
                </span>
                Войти по почте
              </button>
            </div>

            {knownDevice ? (
              <p className="portal-login-consent-note">
                Входя, вы принимаете{" "}
                <a href={PORTAL_ABOUT_PRIVACY_HREF} target="_blank" rel="noreferrer">
                  условия обработки данных
                </a>
              </p>
            ) : (
              <>
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
              </>
            )}

            {emailOpen && (emailStep === "idle" ? (
              <form id="portal-login-email-form" className="portal-login-email" onSubmit={(event) => void handleEmailSubmit(event)}>
                <label className="portal-login-email-label" htmlFor="portal-login-email">
                  Адрес почты
                </label>
                <input
                  id="portal-login-email"
                  type="email"
                  inputMode="email"
                  autoComplete="email"
                  placeholder="you@example.com"
                  value={email}
                  required
                  onChange={(event) => setEmail(event.target.value)}
                />
                <label className="portal-login-news">
                  <input
                    type="checkbox"
                    checked={newsConsent}
                    onChange={(event) => setNewsConsent(event.target.checked)}
                  />
                  <span>Присылать на почту новости проекта: крупные обновления и итоги сезона</span>
                </label>
                <button type="submit" className="btn primary" disabled={emailBusy || !email.trim()}>
                  {emailBusy ? "Отправляем…" : "Получить код"}
                </button>
              </form>
            ) : (
              <form className="portal-login-email" onSubmit={(event) => void handleCodeSubmit(event)}>
                {emailNotice && <p className="portal-login-email-notice">{emailNotice}</p>}
                <label className="portal-login-email-label" htmlFor="portal-login-code">
                  Код из письма
                </label>
                <input
                  id="portal-login-code"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  placeholder="000000"
                  maxLength={6}
                  value={emailCode}
                  required
                  autoFocus
                  onChange={(event) => setEmailCode(event.target.value.replace(/\D/g, ""))}
                />
                <button
                  type="submit"
                  className="btn primary"
                  disabled={emailBusy || emailCode.length < 6}
                >
                  {emailBusy ? "Проверяем…" : "Войти"}
                </button>
                <button
                  type="button"
                  className="portal-login-email-back"
                  onClick={() => {
                    setEmailStep("idle");
                    setEmailCode("");
                    setEmailError(null);
                    setEmailNotice(null);
                    forgetPendingEmail();
                  }}
                >
                  Другой адрес
                </button>
              </form>
            ))}

            {emailError && (
              <p className="portal-login-email-error" role="alert">
                {emailError}
              </p>
            )}


            <div className="portal-login-scopes">
              <p className="portal-login-scopes-title">Что мы получаем при входе</p>
              <ul>
                <li>
                  <VkMark />
                  VK — идентификатор, имя и фамилию
                </li>
                <li>
                  <YandexMark />
                  Яндекс — адрес почты и имя
                </li>
                <li>
                  <MailMark />
                  Почта — только сам адрес
                </li>
              </ul>
              <p className="portal-login-scopes-note">
                Телефон, дату рождения, друзей и ленту сайт{" "}
                <b className="portal-login-scopes-never">не запрашивает и не хранит</b>.{" "}
                <a href={PORTAL_ABOUT_PRIVACY_HREF} target="_blank" rel="noreferrer">
                  Подробнее
                </a>
              </p>
            </div>
          </section>
        </div>
      </main>
      <PortalFooter />
    </>
  );
}
