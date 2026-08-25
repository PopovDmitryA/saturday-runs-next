import { useCallback, useEffect, useMemo, useState } from "react";
import { ParticipantNameSearch } from "../../components/ParticipantNameSearch";
import { RequireAuth } from "../../components/RequireAuth";
import {
  completeOnboarding,
  listProfileLinks,
  setOnboardingNoAccount,
  type PlatformLink,
  type User,
} from "../../lib/api";
import { platformCodeLabel } from "../../lib/format";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { PortalHeader } from "../portal/PortalHeader";

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

function OnboardingContent({ user }: { user: User }) {
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

  const linkedCodes = useMemo(() => new Set(links.map((link) => link.platform_code)), [links]);
  const linkedCount = ONBOARDING_PLATFORMS.filter((code) => linkedCodes.has(code)).length;
  // Привязка сильнее отметки: если профиль появился — «нет аккаунта» не считаем.
  const skippedCount = ONBOARDING_PLATFORMS.filter(
    (code) => !linkedCodes.has(code) && noAccountCodes.has(code),
  ).length;
  const doneCount = linkedCount + skippedCount;
  const total = ONBOARDING_PLATFORMS.length;

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
    window.location.href = "/dashboard";
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
        </section>

        <section className="card onboarding-search-card">
          <h2 className="onboarding-search-title">Найдите себя</h2>
          <p className="muted onboarding-search-sub">
            Имя из протокола, номер участника или штрихкод из QR-кода — например,{" "}
            <span className="onboarding-code-example">A7035519</span>. Ищем сразу по всем системам.
          </p>
          <ParticipantNameSearch
            autoFocus
            linkedPlatformCodes={linkedCodes}
            onLinked={() => void loadLinks()}
          />
          <p className="muted onboarding-fallback">
            Не нашли себя? Профиль можно привязать по ссылке —{" "}
            <a className="link" href="/dashboard#profiles">
              в личном кабинете
            </a>
            .
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

export function OnboardingPage() {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {(user) => <OnboardingContent user={user} />}
    </RequireAuth>
  );
}
