import type { ReactElement } from "react";
import { useEffect } from "react";
import { AdminAbusePage } from "./features/admin/AdminAbusePage";
import { AdminBlockedSlugsPage } from "./features/admin/AdminBlockedSlugsPage";
import { AdminStatsPage } from "./features/admin/AdminStatsPage";
import { AdminSyncRunsPage } from "./features/admin/AdminSyncRunsPage";
import { AdminPageAnalyticsPage } from "./features/admin/AdminPageAnalyticsPage";
import { AdminRatingsPage } from "./features/admin/AdminRatingsPage";
import { AdminEventReportPage } from "./features/admin/AdminEventReportPage";
import { AdminLocationContactsPage } from "./features/admin/AdminLocationContactsPage";
import { AdminRecordsDigestPage } from "./features/admin/AdminRecordsDigestPage";
import { PublicProfilePage } from "./features/public_profile/PublicProfilePage";
import { AdminUsersPage } from "./features/admin/AdminUsersPage";
import { AchievementsPage } from "./features/achievements/AchievementsPage";
import { DashboardPage } from "./features/dashboard/DashboardPage";
import { OAuthCallbackPage } from "./features/auth/OAuthCallbackPage";
import { DemoDashboardPage } from "./features/demo/DemoDashboardPage";
import { PortalAboutPage } from "./features/portal/PortalAboutPage";
import { PortalHomePage } from "./features/portal/PortalHomePage";
import { PortalLoginPage } from "./features/portal/PortalLoginPage";
import { PortalMapLab } from "./features/portal/PortalMapLab";
import {
  PORTAL_ABOUT_HREF,
  PORTAL_CABINET_ACHIEVEMENTS_HREF,
  PORTAL_CABINET_HISTORY_HREF,
  PORTAL_CABINET_HREF,
  PORTAL_CABINET_MAP_HREF,
  PORTAL_CABINET_MEETINGS_HREF,
  PORTAL_CABINET_RUNS_HREF,
  PORTAL_CABINET_VOLUNTEERING_HREF,
  PORTAL_HOME_HREF,
  PORTAL_LOGIN_HREF,
} from "./lib/portalRoutes";
import { PortalCabinetDashboardPage } from "./features/portal/cabinet/PortalCabinetDashboardPage";
import { PortalCabinetPreviewPage } from "./features/portal/cabinet/PortalCabinetPreviewPage";
import {
  PortalCabinetAchievementsPage,
  PortalCabinetHistoryPage,
  PortalCabinetMapPage,
  PortalCabinetMeetingsPage,
  PortalCabinetRunsPage,
  PortalCabinetVolunteeringPage,
} from "./features/portal/cabinet/PortalCabinetPages";
import { DemoMapsPage, MapsPage } from "./features/maps/MapsPage";
import { LocationEventsPage } from "./features/locations/LocationEventsPage";
import { LocationPage } from "./features/locations/LocationPage";
import { LocationsIndexPage } from "./features/locations/LocationsIndexPage";
import { CoRunnersPage, DemoCoRunnersPage } from "./features/co_runners/CoRunnersPage";
import { DemoRunsPage, RunsPage } from "./features/runs/RunsPage";
import { DemoHistoryPage, HistoryPage } from "./features/history/HistoryPage";
import { DemoVolunteeringPage, VolunteeringPage } from "./features/volunteering/VolunteeringPage";
import { LeaderboardPage } from "./features/leaderboards/LeaderboardPage";
import { LeaderboardsHubPage } from "./features/leaderboards/LeaderboardsHubPage";
import { QueuePage } from "./features/queue/QueuePage";
import { SettingsPage } from "./features/settings/SettingsPage";
import { SharePage } from "./features/share/SharePage";
import { NotFoundPage } from "./features/NotFoundPage";
import { LegacySiteBanner } from "./components/LegacySiteBanner";
import { RequireAuth } from "./components/RequireAuth";
import { useAppPath } from "./hooks/useAppPath";
import { getCurrentUser } from "./lib/api";
import { startPageView } from "./lib/pageAnalytics";
import { isLegacyGrafanaPath, legacyGrafanaHref } from "./lib/siteBrand";
import { buildVisitorKey } from "./lib/siteVisitor";

function useSitePageviewTracking(path: string) {
  useEffect(() => {
    let cleanup: (() => void) | null = null;
    let cancelled = false;
    const begin = (authenticated: boolean, userId: string | undefined) => {
      if (!cancelled) {
        cleanup = startPageView(path, authenticated, buildVisitorKey(authenticated, userId));
      }
    };
    getCurrentUser()
      .then((user) => begin(true, user.id))
      .catch(() => begin(false, undefined));
    return () => {
      cancelled = true;
      cleanup?.();
    };
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
  [PORTAL_HOME_HREF]: () => <PortalHomePage />,
  [PORTAL_ABOUT_HREF]: () => <PortalAboutPage />,
  [PORTAL_LOGIN_HREF]: () => <PortalLoginPage />,
  "/new/map-lab": () => <PortalMapLab />,
  // Личный кабинет в портальном дизайне — тёмный запуск под /new/*, рядом со
  // старым кабинетом на канонических адресах. Превью на демо-данных (без
  // логина) — для выбора вариантов дизайна; удалить вместе с /new/* при релизе.
  "/new/cabinet-preview": () => <PortalCabinetPreviewPage />,
  [PORTAL_CABINET_HREF]: () => <PortalCabinetDashboardPage />,
  [PORTAL_CABINET_RUNS_HREF]: () => <PortalCabinetRunsPage />,
  [PORTAL_CABINET_VOLUNTEERING_HREF]: () => <PortalCabinetVolunteeringPage />,
  [PORTAL_CABINET_ACHIEVEMENTS_HREF]: () => <PortalCabinetAchievementsPage />,
  [PORTAL_CABINET_MEETINGS_HREF]: () => <PortalCabinetMeetingsPage />,
  [PORTAL_CABINET_MAP_HREF]: () => <PortalCabinetMapPage />,
  [PORTAL_CABINET_HISTORY_HREF]: () => <PortalCabinetHistoryPage />,
  "/oauth/yandex/callback": () => <OAuthCallbackPage provider="yandex" />,
  "/oauth/vk/callback": () => <OAuthCallbackPage provider="vk" />,
  "/demo": () => <DemoDashboardPage />,
  "/demo/runs": () => <DemoRunsPage />,
  "/demo/co-runners": () => <DemoCoRunnersPage />,
  "/demo/volunteering": () => <DemoVolunteeringPage />,
  "/demo/maps": () => <DemoMapsPage />,
  "/demo/history": () => <DemoHistoryPage />,
  "/dashboard": () => <DashboardPage />,
  "/profiles": () => <DashboardPage />,
  "/runs": () => <RunsPage />,
  "/achievements": () => <AchievementsPage />,
  "/co-runners": () => <CoRunnersPage />,
  "/volunteering": () => <VolunteeringPage />,
  "/maps": () => <MapsPage />,
  // Гейт RequireAuth — внутри самой страницы, как и у /locations/{slug}.
  "/locations": () => <LocationsIndexPage />,
  "/history": () => <HistoryPage />,
  // Раздел для залогиненных: анонима RequireAuth уводит на /login (гейт есть и на API).
  "/ratings": () => <RequireAuth>{() => <LeaderboardsHubPage />}</RequireAuth>,
  "/ratings/runs": () => <RequireAuth>{() => <LeaderboardPage metric="runs" />}</RequireAuth>,
  "/ratings/volunteering": () => (
    <RequireAuth>{() => <LeaderboardPage metric="volunteering" />}</RequireAuth>
  ),
  "/ratings/locations": () => <RequireAuth>{() => <LeaderboardPage metric="locations" />}</RequireAuth>,
  "/share": () => <SharePage />,
  "/sync": () => <SyncRedirect />,
  "/queue": () => <QueueRedirect />,
  "/admin": () => <AdminRedirect />,
  "/admin/queue": () => <QueuePage />,
  "/admin/sync-runs": () => <AdminSyncRunsPage />,
  "/admin/users": () => <AdminUsersPage />,
  "/admin/abuse": () => <AdminAbusePage />,
  "/admin/profile-slugs": () => <AdminBlockedSlugsPage />,
  "/admin/stats": () => <AdminStatsPage />,
  "/admin/page-analytics": () => <AdminPageAnalyticsPage />,
  "/admin/ratings": () => <AdminRatingsPage />,
  "/admin/event-report": () => <AdminEventReportPage />,
  "/admin/records-digest": () => <AdminRecordsDigestPage />,
  "/admin/location-contacts": () => <AdminLocationContactsPage />,
  "/settings": () => <SettingsPage />,
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
  const publicProfileMatch = path.match(/^\/users\/([^/]+)$/);
  if (publicProfileMatch) {
    return <PublicProfilePage handle={decodeURIComponent(publicProfileMatch[1])} />;
  }
  const locationEventsMatch = path.match(/^\/locations\/([^/]+)\/events$/);
  if (locationEventsMatch) {
    return <LocationEventsPage slug={decodeURIComponent(locationEventsMatch[1])} />;
  }
  const locationMatch = path.match(/^\/locations\/([^/]+)$/);
  if (locationMatch) {
    return <LocationPage slug={decodeURIComponent(locationMatch[1])} />;
  }
  const render = STATIC_ROUTES[path];
  if (render) {
    return render();
  }
  return <NotFoundPage />;
}

// Страницы портального редизайна — на них баннер про переезд с Grafana не показываем.
const PORTAL_PATHS = new Set([PORTAL_HOME_HREF, PORTAL_ABOUT_HREF, PORTAL_LOGIN_HREF]);

export function App() {
  const path = useAppPath();
  useSitePageviewTracking(path);
  const hideLegacyBanner = PORTAL_PATHS.has(path) || path.startsWith("/new/");
  return (
    <>
      {!hideLegacyBanner && <LegacySiteBanner />}
      {renderRoute(path)}
    </>
  );
}
