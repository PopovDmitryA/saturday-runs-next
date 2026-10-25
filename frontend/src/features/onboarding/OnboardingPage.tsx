import { useCallback, useEffect, useMemo, useState } from "react";
import { ParticipantNameSearch } from "../../components/ParticipantNameSearch";
import { RequireAuth } from "../../components/RequireAuth";
import { SignupCta } from "../../components/SystemSignupLink";
import {
  completeOnboarding,
  listProfileLinks,
  setOnboardingNoAccount,
  type PlatformLink,
  type User,
} from "../../lib/api";
import { platformCodeLabel } from "../../lib/format";
import { loginHref, readLoginNext } from "../../lib/loginReturn";
import { PORTAL_REGISTRATION_HREF } from "../../lib/portalRoutes";
import { useProfileLinksChanged } from "../../lib/profileLinksEvents";
import { readSignupIntent } from "../../lib/signupIntent";
import { SIGNUP_LINK_HOWTO, isSignupPlatform } from "../../lib/systemSignup";
import { PortalHeader } from "../portal/PortalHeader";
import "../portal/systemRegistration.css";

const ONBOARDING_PLATFORMS = ["five_verst", "s95", "parkrun", "runpark"] as const;

type TileState = "linked" | "no_account" | "empty";

function SystemTile({
  code,
  state,
  busy,
  onToggleNoAccount,
}: {
  code: string;
  state: TileState;
  busy: boolean;
  onToggleNoAccount: () => void;
}) {
  return (
    <div className={`onboarding-system-tile onboarding-system-tile-${state.replace("_", "-")}`}>
      <span className="onboarding-system-check" aria-hidden>
        {state === "no_account" ? (
          <svg viewBox="0 0 24 24" width="14" height="14">
            <path
              fill="none"
              stroke="currentColor"
              strokeWidth="3"
              strokeLinecap="round"
              d="M5 12h14"
            />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" width="14" height="14">
            <path
              fill="none"
              stroke="currentColor"
              strokeWidth="3"
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M4 12.5 9.5 18 20 6.5"
            />
          </svg>
        )}
      </span>
      <span className="onboarding-system-name">{platformCodeLabel(code)}</span>
      <span className="onboarding-system-state">
        {state === "linked" ? "привязано" : state === "no_account" ? "нет аккаунта" : "не привязано"}
      </span>
      {state !== "linked" && (
        <button
          type="button"
          className="onboarding-system-toggle"
          disabled={busy}
          onClick={onToggleNoAccount}
        >
          {state === "no_account" ? "вернуть" : "нет аккаунта?"}
        </button>
      )}
    </div>
  );
}

function OnboardingContent({ user, returnTo }: { user: User; returnTo: string | null }) {
  const [links, setLinks] = useState<PlatformLink[]>([]);
  const [noAccountCodes, setNoAccountCodes] = useState<Set<string>>(
    () => new Set(user.onboarding_no_account_platforms ?? []),
  );
  const [togglingCode, setTogglingCode] = useState<string | null>(null);
  const [finishing, setFinishing] = useState(false);

  const loadLinks = useCallback(async () => {
    try {
      const data = await listProfileLinks();
      setLinks(data);
    } catch {
      // Не блокируем онбординг, если список привязок не загрузился —
      // плитки систем просто останутся пустыми.
    }
  }, []);

  useEffect(() => {
    void loadLinks();
  }, [loadLinks]);

  // Привязали из окна «Это вы?» поверх онбординга — плитки обновятся сами.
  useProfileLinksChanged(() => void loadLinks());

  const linkedCodes = useMemo(() => new Set(links.map((link) => link.platform_code)), [links]);
  const linkedCount = ONBOARDING_PLATFORMS.filter((code) => linkedCodes.has(code)).length;
  // Привязка сильнее отметки: если профиль появился — «нет аккаунта» не считаем.
  const skippedCount = ONBOARDING_PLATFORMS.filter(
    (code) => !linkedCodes.has(code) && noAccountCodes.has(code),
  ).length;
  const doneCount = linkedCount + skippedCount;
  const total = ONBOARDING_PLATFORMS.length;
  // «Нет аккаунта» — повод предложить регистрацию. Но отметка часто неправда:
  // из привязавших потом 5 вёрст большинство бегали там и раньше (разбор
  // 23.10.2026), поэтому рядом — «найдите себя по имени».
  const signupCodes = ONBOARDING_PLATFORMS.filter(
    (code) => !linkedCodes.has(code) && noAccountCodes.has(code),
  ).filter(isSignupPlatform);

  const toggleNoAccount = async (code: string) => {
    const next = !noAccountCodes.has(code);
    setTogglingCode(code);
    // Оптимистично: плитка и ползунок реагируют сразу, откат — только при ошибке.
    setNoAccountCodes((prev) => {
      const updated = new Set(prev);
      if (next) {
        updated.add(code);
      } else {
        updated.delete(code);
      }
      return updated;
    });
    try {
      const response = await setOnboardingNoAccount(code, next);
      setNoAccountCodes(new Set(response.no_account_platforms));
    } catch {
      setNoAccountCodes((prev) => {
        const updated = new Set(prev);
        if (next) {
          updated.delete(code);
        } else {
          updated.add(code);
        }
        return updated;
      });
    } finally {
      setTogglingCode(null);
    }
  };

  const finish = async () => {
    setFinishing(true);
    try {
      await completeOnboarding();
    } catch {
      // Флаг онбординга — не повод застрять на странице: кабинет доступен всегда.
    }
    // Вход начинали с другой страницы — возвращаем туда, онбординг был лишь
    // остановкой по пути (сервер привёл сюда как /welcome?next=…).
    window.location.href = returnTo ?? "/dashboard";
  };

  const firstName = (user.display_name ?? "").trim().split(/\s+/)[0] || null;

  return (
    <>
      <PortalHeader />
      <main className="app onboarding-page">
        <header className="onboarding-head">
          <h1 className="onboarding-title">
            {firstName ? `Добро пожаловать, ${firstName}!` : "Добро пожаловать!"}
          </h1>
          <p className="onboarding-lead">
            Соберём всю вашу беговую историю в одном кабинете — найдите себя, и статистика подтянется
            сама.
          </p>
        </header>

        <section className="onboarding-systems-block" aria-label="Прогресс привязки систем">
          <div className="onboarding-systems">
            {ONBOARDING_PLATFORMS.map((code) => {
              const state: TileState = linkedCodes.has(code)
                ? "linked"
                : noAccountCodes.has(code)
                  ? "no_account"
                  : "empty";
              return (
                <SystemTile
                  key={code}
                  code={code}
                  state={state}
                  busy={togglingCode === code}
                  onToggleNoAccount={() => void toggleNoAccount(code)}
                />
              );
            })}
          </div>
          <div className="onboarding-progress-row">
            <div
              className="onboarding-progress-track"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={total}
              aria-valuenow={doneCount}
            >
              <div
                className="onboarding-progress-fill"
                style={{ width: `${(linkedCount / total) * 100}%` }}
              />
              <div
                className="onboarding-progress-fill onboarding-progress-fill-skipped"
                style={{ width: `${(skippedCount / total) * 100}%` }}
              />
            </div>
            <span className="onboarding-progress-count">
              {doneCount} из {total}
            </span>
          </div>
          {signupCodes.length > 0 && (
            <div className="onboarding-signup">
              <p>
                Профиль заводится на сайте системы, это бесплатно. Зарегистрируйтесь и вернитесь
                сюда:
              </p>
              <ul>
                {signupCodes.map((code) => (
                  <li key={code}>
                    <SignupCta code={code} place="onboarding" withName /> — потом{" "}
                    {SIGNUP_LINK_HOWTO[code]}.
                  </li>
                ))}
              </ul>
              <p className="muted">
                Уже бегали, но не помните номер? Найдите себя по имени из протокола в поле ниже.
              </p>
            </div>
          )}
        </section>

        <section className="card onboarding-search-card">
          <h2 className="onboarding-search-title">Найдите себя</h2>
          <p className="muted onboarding-search-sub">
            Одно поле на все системы: имя из протокола, код участника — например,{" "}
            <span className="onboarding-code-example">A7035519</span> — или ссылка на ваш профиль.
          </p>
          <ParticipantNameSearch
            autoFocus
            linkedPlatformCodes={linkedCodes}
            onLinked={() => void loadLinks()}
          />
          <p className="muted onboarding-fallback">
            Не нашли себя? Вставьте в это же поле ссылку на профиль с сайта системы — например,
            https://5verst.ru/userstats/… — мы загрузим его и привяжем.
          </p>
        </section>

        <div className="onboarding-actions">
          <button
            type="button"
            className="btn primary onboarding-cta"
            disabled={finishing}
            onClick={() => void finish()}
          >
            {finishing
              ? "Переходим…"
              : returnTo
                ? doneCount > 0
                  ? "Готово — вернуться на страницу"
                  : "Вернуться на страницу"
                : doneCount > 0
                  ? "Готово — в личный кабинет"
                  : "Перейти в личный кабинет"}
          </button>
          {doneCount === 0 && (
            <button
              type="button"
              className="btn btn-ghost"
              disabled={finishing}
              onClick={() => void finish()}
            >
              Пропустить пока
            </button>
          )}
        </div>
      </main>
    </>
  );
}

// Вошли, чтобы привязать номер, полученный при регистрации в системе: человек
// уже знает, какой профиль его, — онбординг пропускаем и сразу возвращаем на
// /registration, где форма покажет «Это ваш профиль?» (lib/signupIntent).
function shouldSkipToSignupLink(returnTo: string | null): boolean {
  return Boolean(returnTo?.startsWith(PORTAL_REGISTRATION_HREF) && readSignupIntent()?.input);
}

export function OnboardingPage() {
  // Страница, откуда начинали вход: /welcome?next=… (onboarding_service).
  const [returnTo] = useState<string | null>(() => readLoginNext());
  const [skip] = useState(() => shouldSkipToSignupLink(returnTo));
  useEffect(() => {
    if (skip && returnTo) {
      window.location.replace(returnTo);
    }
  }, [skip, returnTo]);
  if (skip) {
    return null;
  }
  return (
    // Сессия пропала прямо на онбординге — после входа вернём туда же, куда
    // онбординг вёл бы и сам: сам /welcome адресом возврата не бывает.
    <RequireAuth loginHref={loginHref(returnTo)}>
      {(user) => <OnboardingContent user={user} returnTo={returnTo} />}
    </RequireAuth>
  );
}
