import { useCallback, useEffect, useMemo, useState } from "react";
import { ParticipantNameSearch } from "../../components/ParticipantNameSearch";
import { RequireAuth } from "../../components/RequireAuth";
import { completeOnboarding, listProfileLinks, type PlatformLink, type User } from "../../lib/api";
import { platformCodeLabel } from "../../lib/format";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { PortalHeader } from "../portal/PortalHeader";

const ONBOARDING_PLATFORMS = ["five_verst", "s95", "parkrun", "runpark"] as const;

function SystemTile({ code, done }: { code: string; done: boolean }) {
  return (
    <div className={`onboarding-system-tile${done ? " onboarding-system-tile-done" : ""}`}>
      <span className="onboarding-system-check" aria-hidden>
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
      </span>
      <span className="onboarding-system-name">{platformCodeLabel(code)}</span>
      <span className="onboarding-system-state">{done ? "привязано" : "не привязано"}</span>
    </div>
  );
}

function OnboardingContent({ user }: { user: User }) {
  const [links, setLinks] = useState<PlatformLink[]>([]);
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
  const progressPct = (linkedCount / ONBOARDING_PLATFORMS.length) * 100;

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
            {ONBOARDING_PLATFORMS.map((code) => (
              <SystemTile key={code} code={code} done={linkedCodes.has(code)} />
            ))}
          </div>
          <div className="onboarding-progress-row">
            <div
              className="onboarding-progress-track"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={ONBOARDING_PLATFORMS.length}
              aria-valuenow={linkedCount}
            >
              <div className="onboarding-progress-fill" style={{ width: `${progressPct}%` }} />
            </div>
            <span className="onboarding-progress-count">
              {linkedCount} из {ONBOARDING_PLATFORMS.length}
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
              : linkedCount > 0
                ? "Готово — в личный кабинет"
                : "Перейти в личный кабинет"}
          </button>
          {linkedCount === 0 && (
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
