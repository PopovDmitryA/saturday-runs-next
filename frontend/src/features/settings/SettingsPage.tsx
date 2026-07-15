import { useMemo } from "react";
import { AppShell } from "../../components/AppShell";
import { AuthProvidersSection } from "./AuthProvidersSection";
import { HistoryMilestonesSection } from "./HistoryMilestonesSection";
import { HomeLocationSection } from "./HomeLocationSection";
import { PrivacySettingsSection } from "./PrivacySettingsSection";
import { ProfileLinkSection } from "./ProfileLinkSection";
import { RequireAuth } from "../../components/RequireAuth";

function SettingsContent() {
  const mergeToken = useMemo(
    () => new URLSearchParams(window.location.search).get("merge_token"),
    [],
  );

  return (
    <AppShell title="Настройки" activePath="/settings">
      <PrivacySettingsSection />
      <ProfileLinkSection />
      <HomeLocationSection />
      <HistoryMilestonesSection />
      <AuthProvidersSection initialMergeToken={mergeToken} />
    </AppShell>
  );
}

export function SettingsPage() {
  return <RequireAuth>{() => <SettingsContent />}</RequireAuth>;
}
