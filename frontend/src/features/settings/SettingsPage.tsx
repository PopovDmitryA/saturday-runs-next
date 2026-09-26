import { useMemo } from "react";
import { AuthProvidersSection } from "./AuthProvidersSection";
import { AvatarSection } from "./AvatarSection";
import { DisplayNameSection } from "./DisplayNameSection";
import { HomeLocationSection } from "./HomeLocationSection";
import { NewsletterSection } from "./NewsletterSection";
import { PrivacySettingsSection } from "./PrivacySettingsSection";
import { ProfileLinkSection } from "./ProfileLinkSection";

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
      <NewsletterSection />
      <ProfileLinkSection />
      <HomeLocationSection />
      <AuthProvidersSection initialMergeToken={mergeToken} />
    </>
  );

  return <div className="portal-cab-stack">{pageBody}</div>;
}

