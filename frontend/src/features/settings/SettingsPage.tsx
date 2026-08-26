import { useMemo } from "react";
import { AppShell } from "../../components/AppShell";
import { AuthProvidersSection } from "./AuthProvidersSection";
import { AvatarSection } from "./AvatarSection";
import { DisplayNameSection } from "./DisplayNameSection";
import { HistoryMilestonesSection } from "./HistoryMilestonesSection";
import { HomeLocationSection } from "./HomeLocationSection";
import { NotificationSettingsSection } from "./NotificationSettingsSection";
import { PrivacySettingsSection } from "./PrivacySettingsSection";
import { ProfileLinkSection } from "./ProfileLinkSection";

// bare — отдать только тело страницы, без AppShell: портальный ЛК (/new/*)
// оборачивает контент в собственный каркас с сайдбаром.
export function SettingsContent({ bare = false }: { bare?: boolean } = {}) {
  const mergeToken = useMemo(
    () => new URLSearchParams(window.location.search).get("merge_token"),
    [],
  );

  const pageBody = (
    <>
      <AvatarSection />
      <DisplayNameSection />
      <PrivacySettingsSection />
      <NotificationSettingsSection />
      <ProfileLinkSection />
      <HomeLocationSection />
      <HistoryMilestonesSection />
      <AuthProvidersSection initialMergeToken={mergeToken} />
    </>
  );

  if (bare) {
    return <div className="portal-cab-stack">{pageBody}</div>;
  }

  return (
    <AppShell title="Настройки" activePath="/settings">
      {pageBody}
    </AppShell>
  );
}

