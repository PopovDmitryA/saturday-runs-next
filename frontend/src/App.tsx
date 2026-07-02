import type { ReactElement } from "react";
import { useEffect } from "react";
import { AdminAbusePage } from "./features/admin/AdminAbusePage";
import { AdminS95ParticipantsPage } from "./features/admin/AdminS95ParticipantsPage";
import { AdminParkrunPage } from "./features/admin/AdminParkrunPage";
import { AdminStatsPage } from "./features/admin/AdminStatsPage";
import { AdminUserPreviewPage } from "./features/admin/AdminUserPreviewPage";
import { PublicProfilePage } from "./features/public_profile/PublicProfilePage";
import { AdminUsersPage } from "./features/admin/AdminUsersPage";
import { DashboardPage } from "./features/dashboard/DashboardPage";
import { LoginPage } from "./features/auth/LoginPage";
import { OAuthCallbackPage } from "./features/auth/OAuthCallbackPage";
import { DemoDashboardPage } from "./features/demo/DemoDashboardPage";
import { LandingPage } from "./features/landing/LandingPage";
import { DemoMapsPage, MapsPage } from "./features/maps/MapsPage";
import { CoRunnersPage, DemoCoRunnersPage } from "./features/co_runners/CoRunnersPage";
import { DemoRunsPage, RunsPage } from "./features/runs/RunsPage";
import { DemoVolunteeringPage, VolunteeringPage } from "./features/volunteering/VolunteeringPage";
import { QueuePage } from "./features/queue/QueuePage";
import { SettingsPage } from "./features/settings/SettingsPage";
import { SharePage } from "./features/share/SharePage";
import { AboutPage } from "./features/about/AboutPage";
import { NotFoundPage } from "./features/NotFoundPage";
import { LegacySiteBanner } from "./components/LegacySiteBanner";
import { useAppPath } from "./hooks/useAppPath";
import { getCurrentUser, recordSitePageview } from "./lib/api";
import { isLegacyGrafanaPath, legacyGrafanaHref } from "./lib/siteBrand";
import { buildVisitorKey } from "./lib/siteVisitor";

function useSitePageviewTracking(path: string) {
  useEffect(() => {
    getCurrentUser()
      .then((user) => recordSitePageview(path, true, buildVisitorKey(true, user.id)))
      .catch(() => recordSitePageview(path, false, buildVisitorKey(false, undefined)));
  }, [path]);
}

function SyncRedirect() {
  useEffect(() => {
    window.location.replace("/dashboard#profiles");
  }, []);
  return null;
}

function QueueRedirect() {
  useEffect(() => {
    window.location.replace("/admin/queue");
  }, []);
  return null;
}

function AdminRedirect() {
  useEffect(() => {
    window.location.replace("/admin/users");
  }, []);
  return null;
}

const STATIC_ROUTES: Record<string, () => ReactElement> = {
  "/": () => <LandingPage />,
  "/login": () => <LoginPage />,
  "/oauth/yandex/callback": () => <OAuthCallbackPage provider="yandex" />,
  "/oauth/vk/callback": () => <OAuthCallbackPage provider="vk" />,
  "/demo": () => <DemoDashboardPage />,
  "/demo/runs": () => <DemoRunsPage />,
  "/demo/co-runners": () => <DemoCoRunnersPage />,
  "/demo/volunteering": () => <DemoVolunteeringPage />,
  "/demo/maps": () => <DemoMapsPage />,
  "/dashboard": () => <DashboardPage />,
  "/profiles": () => <DashboardPage />,
  "/runs": () => <RunsPage />,
  "/co-runners": () => <CoRunnersPage />,
  "/volunteering": () => <VolunteeringPage />,
  "/maps": () => <MapsPage />,
  "/share": () => <SharePage />,
  "/sync": () => <SyncRedirect />,
  "/queue": () => <QueueRedirect />,
  "/admin": () => <AdminRedirect />,
  "/admin/queue": () => <QueuePage />,
  "/admin/users": () => <AdminUsersPage />,
  "/admin/s95-participants": () => <AdminS95ParticipantsPage />,
  "/admin/abuse": () => <AdminAbusePage />,
  "/admin/stats": () => <AdminStatsPage />,
  "/admin/parkrun": () => <AdminParkrunPage />,
  "/settings": () => <SettingsPage />,
  "/about": () => <AboutPage />,
};

function LegacyGrafanaRedirect() {
  useEffect(() => {
    const target = legacyGrafanaHref(
      window.location.pathname,
      window.location.search,
      window.location.hash,
    );
    window.location.replace(target);
  }, []);
  return (
    <main className="app">
      <p className="muted">Переход на прежний сайт (Grafana)…</p>
    </main>
  );
}

function ApiPathRedirect() {
  useEffect(() => {
    window.location.replace(`${window.location.pathname}${window.location.search}${window.location.hash}`);
  }, []);
  return (
    <main className="app">
      <p className="muted">Переход…</p>
    </main>
  );
}

function renderRoute(path: string): ReactElement {
  if (isLegacyGrafanaPath(path)) {
    return <LegacyGrafanaRedirect />;
  }
  if (path.startsWith("/api/")) {
    return <ApiPathRedirect />;
  }
  const previewMatch = path.match(/^\/admin\/users\/([0-9a-f-]{36})\/preview$/i);
  if (previewMatch) {
    return <AdminUserPreviewPage userId={previewMatch[1]} />;
  }
  const publicProfileMatch = path.match(/^\/users\/(\d+)$/);
  if (publicProfileMatch) {
    return <PublicProfilePage serialId={Number(publicProfileMatch[1])} />;
  }
  const render = STATIC_ROUTES[path];
  if (render) {
    return render();
  }
  return <NotFoundPage />;
}

export function App() {
  const path = useAppPath();
  useSitePageviewTracking(path);
  return (
    <>
      <LegacySiteBanner />
      {renderRoute(path)}
    </>
  );
}
