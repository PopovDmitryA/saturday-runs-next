import { useMemo } from "react";
import { PLAN_NOTIFICATIONS_HREF, PLANNER_SETTINGS_ANCHOR } from "../planner/plannerLinks";
import { AuthProvidersSection } from "./AuthProvidersSection";
import { AvatarSection } from "./AvatarSection";
import { DeleteAccountSection } from "./DeleteAccountSection";
import { DisplayNameSection } from "./DisplayNameSection";
import { HomeLocationSection } from "./HomeLocationSection";
import { NewsletterSection } from "./NewsletterSection";
import { PrivacySettingsSection } from "./PrivacySettingsSection";
import { ProfileLinkSection } from "./ProfileLinkSection";

/**
 * Уведомления о планах живут в самих планах (решения Дмитрия 26–27.10.2026):
 * здесь — только ссылка. Кто видит план, выбирают в окне плана. Якорь
 * #planner остаётся — по нему ведут прежние ссылки.
 */
function PlannerSettingsLink() {
  return (
    <section className="card planner-settings-link-card" id={PLANNER_SETTINGS_ANCHOR}>
      <h2 className="section-title">Мои планы</h2>
      <p className="muted settings-lead">
        <a className="link" href={PLAN_NOTIFICATIONS_HREF}>
          Уведомления о планах
        </a>{" "}
        — в «Моих планах»; кто видит план, выбираете в самом плане.
      </p>
    </section>
  );
}

// Тело страницы без каркаса: шапку, рельс и колонку рисует тот, кто
// вставляет контент (кабинет — PortalCabinetShell, чужой профиль — свой
// каркас).
export function SettingsContent() {
  const mergeToken = useMemo(
    () => new URLSearchParams(window.location.search).get("merge_token"),
    [],
  );

  const pageBody = (
    <>
      <AvatarSection />
      <DisplayNameSection />
      <PrivacySettingsSection />
      <PlannerSettingsLink />
      <NewsletterSection />
      <ProfileLinkSection />
      <HomeLocationSection />
      <AuthProvidersSection initialMergeToken={mergeToken} />
      <DeleteAccountSection />
    </>
  );

  return <div className="portal-cab-stack">{pageBody}</div>;
}

