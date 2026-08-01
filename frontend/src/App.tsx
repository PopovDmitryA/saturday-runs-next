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
import { ProfileRoute } from "./features/profile/ProfileRoute";
import { AdminUsersPage } from "./features/admin/AdminUsersPage";
import { OAuthCallbackPage } from "./features/auth/OAuthCallbackPage";
import { PortalAboutPage } from "./features/portal/PortalAboutPage";
import { PortalBlogPage } from "./features/portal/PortalBlogPage";
import { PortalHomePage } from "./features/portal/PortalHomePage";
import { PortalLoginPage } from "./features/portal/PortalLoginPage";
import { PortalMapLab } from "./features/portal/PortalMapLab";
import { AdminBlogPage } from "./features/admin/AdminBlogPage";
import { AdminBacklogPage } from "./features/admin/AdminBacklogPage";
import { BacklogPage } from "./features/backlog/BacklogPage";
import {
  cabinetTabHref,
  profileBaseHref,
  type CabinetTabSegmentKey,
  PORTAL_ABOUT_HREF,
  PORTAL_BLOG_HREF,
  PORTAL_CABINET_ACHIEVEMENTS_HREF,
  PORTAL_CABINET_HISTORY_HREF,
  PORTAL_CABINET_HREF,
  PORTAL_CABINET_MAP_HREF,
  PORTAL_CABINET_MEETINGS_HREF,
  PORTAL_CABINET_RUNS_HREF,
  PORTAL_CABINET_SETTINGS_HREF,
  PORTAL_CABINET_SHARE_HREF,
  PORTAL_CABINET_VOLUNTEERING_HREF,
  PORTAL_HOME_HREF,
  PORTAL_LOGIN_HREF,
} from "./lib/portalRoutes";
import { PortalCabinetPreviewPage } from "./features/portal/cabinet/PortalCabinetPreviewPage";
import {
  PortalCabinetSettingsPage,
  PortalCabinetSharePage,
} from "./features/portal/cabinet/PortalCabinetPages";
import { LocationEventsPage } from "./features/locations/LocationEventsPage";
import { LocationPage } from "./features/locations/LocationPage";
import { LocationsIndexPage } from "./features/locations/LocationsIndexPage";
import { LeaderboardPage } from "./features/leaderboards/LeaderboardPage";
import { LeaderboardsHubPage } from "./features/leaderboards/LeaderboardsHubPage";
import { QueuePage } from "./features/queue/QueuePage";
import { SweepHqPage } from "./features/sweep_hq/SweepHqPage";
import { SweepWorldPage } from "./features/sweep_hq/SweepWorldPage";
import { NotFoundPage } from "./features/NotFoundPage";
import { useAppPath } from "./hooks/useAppPath";
import { reportAbLoginOnce } from "./lib/abTest";
import { getCurrentUser } from "./lib/api";
import { useOptionalUser } from "./lib/useOptionalUser";
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
      .then((user) => {
        begin(true, user.id);
        // АБ-воронка: первый авторизованный визит в этом браузере — событие
        // login_complete (когорту new/returning определяет сервер).
        reportAbLoginOnce(user.id);
      })
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

/**
 * Старые служебные адреса кабинета (/new/dashboard и др.) переводят на
 * публичный адрес участника — /users/{хендл}[/вкладка]. Аноним уходит на вход.
 */
function CabinetLegacyRedirect({ tab }: { tab: CabinetTabSegmentKey }) {
  const user = useOptionalUser({ skipCache: true });
  useEffect(() => {
    if (user === undefined) {
      return;
    }
    if (user === null) {
      window.location.replace(PORTAL_LOGIN_HREF);
      return;
    }
    const target = profileBaseHref(user) ? cabinetTabHref(user, tab) : null;
    // Без хендла (профиль ещё не получил номер) оставляем старый экран.
    if (target) {
      window.location.replace(target);
    }
  }, [user, tab]);
  return (
    <main className="app">
      <p className="muted">Открываем кабинет…</p>
    </main>
  );
}

function PathRedirect({ to }: { to: string }) {
  useEffect(() => {
    window.location.replace(to);
  }, [to]);
  return (
    <main className="app">
      <p className="muted">Переход…</p>
    </main>
  );
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
  [PORTAL_BLOG_HREF]: () => <PortalBlogPage />,
  "/new/map-lab": () => <PortalMapLab />,
  // Личный кабинет в портальном дизайне — тёмный запуск под /new/*, рядом со
  // старым кабинетом на канонических адресах. Превью на демо-данных (без
  // логина) — для выбора вариантов дизайна; удалить вместе с /new/* при релизе.
  "/new/cabinet-preview": () => <PortalCabinetPreviewPage />,
  [PORTAL_CABINET_HREF]: () => <CabinetLegacyRedirect tab="dashboard" />,
  [PORTAL_CABINET_RUNS_HREF]: () => <CabinetLegacyRedirect tab="runs" />,
  [PORTAL_CABINET_VOLUNTEERING_HREF]: () => <CabinetLegacyRedirect tab="volunteering" />,
  [PORTAL_CABINET_ACHIEVEMENTS_HREF]: () => <CabinetLegacyRedirect tab="achievements" />,
  [PORTAL_CABINET_MEETINGS_HREF]: () => <CabinetLegacyRedirect tab="meetings" />,
  [PORTAL_CABINET_MAP_HREF]: () => <CabinetLegacyRedirect tab="map" />,
  [PORTAL_CABINET_HISTORY_HREF]: () => <CabinetLegacyRedirect tab="history" />,
  [PORTAL_CABINET_SHARE_HREF]: () => <PortalCabinetSharePage />,
  [PORTAL_CABINET_SETTINGS_HREF]: () => <PortalCabinetSettingsPage />,
  // Адреса тёмного запуска остаются работающими ссылками.
  "/new/share": () => <PathRedirect to={PORTAL_CABINET_SHARE_HREF} />,
  "/new/settings": () => <PathRedirect to={PORTAL_CABINET_SETTINGS_HREF} />,
  "/oauth/yandex/callback": () => <OAuthCallbackPage provider="yandex" />,
  "/oauth/vk/callback": () => <OAuthCallbackPage provider="vk" />,
  // Старые адреса кабинета уводят на публичный адрес участника. Демо-режим
  // удалён вместе со старым дизайном (решение Дмитрия 26.07.2026).
  "/dashboard": () => <CabinetLegacyRedirect tab="dashboard" />,
  "/profiles": () => <CabinetLegacyRedirect tab="dashboard" />,
  "/runs": () => <CabinetLegacyRedirect tab="runs" />,
  "/achievements": () => <CabinetLegacyRedirect tab="achievements" />,
  "/co-runners": () => <CabinetLegacyRedirect tab="meetings" />,
  "/volunteering": () => <CabinetLegacyRedirect tab="volunteering" />,
  "/maps": () => <CabinetLegacyRedirect tab="map" />,
  // Локации открыты без логина (25.07.2026) — публичная витрина.
  "/locations": () => <LocationsIndexPage />,
  "/history": () => <CabinetLegacyRedirect tab="history" />,
  // Рейтинги открыты без логина (решение 25.07.2026): аноним видит таблицы,
  // а свою строку и позицию — только залогиненный (баннер-призыв на страницах).
  "/ratings": () => <LeaderboardsHubPage />,
  "/ratings/runs": () => <LeaderboardPage metric="runs" />,
  "/ratings/volunteering": () => <LeaderboardPage metric="volunteering" />,
  "/ratings/volunteer-roles": () => <LeaderboardPage metric="volunteer_roles" />,
  "/ratings/locations": () => <LeaderboardPage metric="locations" />,
  "/ratings/volunteer-locations": () => <LeaderboardPage metric="volunteer_locations" />,
  "/ratings/wins": () => <LeaderboardPage metric="wins" />,
  "/ratings/win-locations": () => <LeaderboardPage metric="win_locations" />,
  // Просмотр открыт всем; писать (карточка/голос/комментарий) может только
  // залогиненный — гейт внутри самой страницы, как у /locations.
  "/backlog": () => <BacklogPage />,

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
  "/admin/blog": () => <AdminBlogPage />,
  "/admin/backlog": () => <AdminBacklogPage />,

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
  const sweepHqMatch = path.match(/^\/hq\/(.+)$/);
  if (sweepHqMatch) {
    return <SweepHqPage token={decodeURIComponent(sweepHqMatch[1])} />;
  }
  // Публичная витрина обхода — без имён, прокси и счётчиков капч (см. /hq).
  if (path === "/world" || path === "/world/") {
    return <SweepWorldPage />;
  }
  // Публичный адрес участника = адрес его кабинета: свой хендл открывает
  // кабинет, чужой — гостевой профиль (см. ProfileRoute).
  const profileMatch = path.match(/^\/users\/([^/]+)(?:\/([^/]+))?$/);
  if (profileMatch) {
    return (
      <ProfileRoute
        handle={decodeURIComponent(profileMatch[1])}
        segment={profileMatch[2] ? decodeURIComponent(profileMatch[2]) : undefined}
      />
    );
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

export function App() {
  const path = useAppPath();
  useSitePageviewTracking(path);
  return renderRoute(path);
}
