import { useMemo } from "react";
import { AppShell } from "../../components/AppShell";
import { AuthProvidersSection } from "./AuthProvidersSection";
import { AvatarSection } from "./AvatarSection";
import { HistoryMilestonesSection } from "./HistoryMilestonesSection";
import { HomeLocationSection } from "./HomeLocationSection";
import { PrivacySettingsSection } from "./PrivacySettingsSection";
import { ProfileLinkSection } from "./ProfileLinkSection";
import { RequireAuth } from "../../components/RequireAuth";

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
      <PrivacySettingsSection />
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

export function SettingsPage() {
  return <RequireAuth>{() => <SettingsContent />}</RequireAuth>;
}
