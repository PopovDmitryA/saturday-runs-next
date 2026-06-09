import type { ReactElement } from "react";
import { useEffect } from "react";
import { AdminAbusePage } from "./features/admin/AdminAbusePage";
import { AdminPage } from "./features/admin/AdminPage";
import { AdminS95ParticipantsPage } from "./features/admin/AdminS95ParticipantsPage";
import { AdminParkrunPage } from "./features/admin/AdminParkrunPage";
import { AdminStatsPage } from "./features/admin/AdminStatsPage";
import { AdminUserPreviewPage } from "./features/admin/AdminUserPreviewPage";
import { AdminUsersPage } from "./features/admin/AdminUsersPage";
import { DashboardPage } from "./features/dashboard/DashboardPage";
import { LoginPage } from "./features/auth/LoginPage";
import { OAuthCallbackPage } from "./features/auth/OAuthCallbackPage";
import { DemoDashboardPage } from "./features/demo/DemoDashboardPage";
import { LandingPage } from "./features/landing/LandingPage";
import { DemoMapsPage, MapsPage } from "./features/maps/MapsPage";
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

const STATIC_ROUTES: Record<string, () => ReactElement> = {
  "/": () => <LandingPage />,
  "/login": () => <LoginPage />,
  "/oauth/yandex/callback": () => <OAuthCallbackPage provider="yandex" />,
  "/oauth/vk/callback": () => <OAuthCallbackPage provider="vk" />,
  "/demo": () => <DemoDashboardPage />,
  "/demo/runs": () => <DemoRunsPage />,
  "/demo/volunteering": () => <DemoVolunteeringPage />,
  "/demo/maps": () => <DemoMapsPage />,
  "/dashboard": () => <DashboardPage />,
  "/profiles": () => <DashboardPage />,
  "/runs": () => <RunsPage />,
  "/volunteering": () => <VolunteeringPage />,
  "/maps": () => <MapsPage />,
  "/share": () => <SharePage />,
  "/sync": () => <SyncRedirect />,
  "/queue": () => <QueueRedirect />,
  "/admin": () => <AdminPage />,
  "/admin/queue": () => <QueuePage />,
  "/admin/users": () => <AdminUsersPage />,
  "/admin/s95-participants": () => <AdminS95ParticipantsPage />,
  "/admin/abuse": () => <AdminAbusePage />,
  "/admin/stats": () => <AdminStatsPage />,
  "/admin/parkrun": () => <AdminParkrunPage />,
  "/settings": () => <SettingsPage />,
  "/about": () => <AboutPage />,
};

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
  if (path.startsWith("/api/")) {
    return <ApiPathRedirect />;
  }
  const previewMatch = path.match(/^\/admin\/users\/([0-9a-f-]{36})\/preview$/i);
  if (previewMatch) {
    return <AdminUserPreviewPage userId={previewMatch[1]} />;
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
