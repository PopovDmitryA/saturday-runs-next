import { useMemo } from "react";
import { AppShell } from "../../components/AppShell";
import { AuthProvidersSection } from "./AuthProvidersSection";
import { PrivacySettingsSection } from "./PrivacySettingsSection";
import { RequireAuth } from "../../components/RequireAuth";

function SettingsContent() {
  const mergeToken = useMemo(
    () => new URLSearchParams(window.location.search).get("merge_token"),
    [],
  );

  return (
    <AppShell title="Настройки" activePath="/settings">
      <PrivacySettingsSection />
      <AuthProvidersSection initialMergeToken={mergeToken} />
    </AppShell>
  );
}

export function SettingsPage() {
  return <RequireAuth>{() => <SettingsContent />}</RequireAuth>;
}
