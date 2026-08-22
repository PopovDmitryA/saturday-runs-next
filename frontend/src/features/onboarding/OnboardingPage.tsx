import { useCallback, useEffect, useMemo, useState } from "react";
import { ParticipantNameSearch } from "../../components/ParticipantNameSearch";
import { RequireAuth } from "../../components/RequireAuth";
import { completeOnboarding, listProfileLinks, type PlatformLink, type User } from "../../lib/api";
import { platformCodeLabel } from "../../lib/format";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { PortalHeader } from "../portal/PortalHeader";

const ONBOARDING_PLATFORMS = ["five_verst", "s95", "parkrun", "runpark"] as const;

function OnboardingContent({ user }: { user: User }) {
  const [links, setLinks] = useState<PlatformLink[]>([]);
  const [linksLoaded, setLinksLoaded] = useState(false);
  const [finishing, setFinishing] = useState(false);

  const loadLinks = useCallback(async () => {
    try {
      const data = await listProfileLinks();
      setLinks(data);
    } catch {
      // Не блокируем онбординг, если список привязок не загрузился —
      // прогресс-чипы просто останутся пустыми.
    } finally {
      setLinksLoaded(true);
    }
  }, []);

  useEffect(() => {
    void loadLinks();
  }, [loadLinks]);

  const linkedCodes = useMemo(() => new Set(links.map((link) => link.platform_code)), [links]);
  const linkedCount = ONBOARDING_PLATFORMS.filter((code) => linkedCodes.has(code)).length;

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
        <h1>{firstName ? `Добро пожаловать, ${firstName}!` : "Добро пожаловать!"}</h1>

        <section className="card onboarding-hero">
          <p className="onboarding-hero-lead">
            Соберём всю вашу беговую историю в одном кабинете. Найдите себя по имени — мы поищем сразу
            во всех системах: 5 вёрст, С95, parkrun и RunPark.
          </p>
          <div className="onboarding-progress" role="status">
            {ONBOARDING_PLATFORMS.map((code) => (
              <span
                key={code}
                className={`onboarding-progress-chip${linkedCodes.has(code) ? " onboarding-progress-chip-done" : ""}`}
              >
                {linkedCodes.has(code) ? "✓ " : ""}
                {platformCodeLabel(code)}
              </span>
            ))}
          </div>
          {linksLoaded && linkedCount > 0 && (
            <p className="onboarding-progress-note">
              Привязано систем: {linkedCount} из {ONBOARDING_PLATFORMS.length}. Можно продолжить поиск
              или перейти в кабинет.
            </p>
          )}
        </section>

        <section className="card onboarding-search-card">
          <h2 className="section-title">Найдите себя по имени</h2>
          <ParticipantNameSearch
            autoFocus
            linkedPlatformCodes={linkedCodes}
            onLinked={() => void loadLinks()}
          />
          <p className="muted onboarding-fallback">
            Не нашли себя? Профиль можно привязать по ссылке или штрихкоду —{" "}
            <a className="link" href="/dashboard#profiles">
              в личном кабинете
            </a>
            .
          </p>
        </section>

        <div className="onboarding-actions">
          <button
            type="button"
            className="btn primary"
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
