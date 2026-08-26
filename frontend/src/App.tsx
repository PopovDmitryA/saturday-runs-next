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
import { AdminLocationOpeningsPage } from "./features/admin/AdminLocationOpeningsPage";
import { AdminRecordsDigestPage } from "./features/admin/AdminRecordsDigestPage";
import { ProfileRoute } from "./features/profile/ProfileRoute";
import { AdminUsersPage } from "./features/admin/AdminUsersPage";
import { OAuthCallbackPage } from "./features/auth/OAuthCallbackPage";
import { OnboardingPage } from "./features/onboarding/OnboardingPage";
import { PortalAboutPage } from "./features/portal/PortalAboutPage";
import { PortalBlogPage } from "./features/portal/PortalBlogPage";
import { PortalHomePage } from "./features/portal/PortalHomePage";
import { PortalLoginPage } from "./features/portal/PortalLoginPage";
import { PortalMapLab } from "./features/portal/PortalMapLab";
import { PortalUpdatesPage } from "./features/portal/PortalUpdatesPage";
import { AdminBlogPage } from "./features/admin/AdminBlogPage";
import { AdminReleasesPage } from "./features/admin/AdminReleasesPage";
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
  PORTAL_UPDATES_HREF,
} from "./lib/portalRoutes";
import {
  PortalCabinetSettingsPage,
  PortalCabinetSharePage,
} from "./features/portal/cabinet/PortalCabinetPages";
import { LocationEventsPage } from "./features/locations/LocationEventsPage";
import { LocationProtocolPage } from "./features/locations/LocationProtocolPage";
import { LocationPage } from "./features/locations/LocationPage";
import { LastResultsPage } from "./features/locations/LastResultsPage";
import { LocationsIndexPage } from "./features/locations/LocationsIndexPage";
import { FastestRatingPage } from "./features/leaderboards/FastestRatingPage";
import { UnifiedProtocolPage } from "./features/locations/UnifiedProtocolPage";
import { LeaderboardPage } from "./features/leaderboards/LeaderboardPage";
import { LeaderboardsHubPage } from "./features/leaderboards/LeaderboardsHubPage";
import { OrganizerAbsencePage } from "./features/organizer/OrganizerAbsencePage";
import { OrganizerAttendancePage } from "./features/organizer/OrganizerAttendancePage";
import { OrganizerProtocolsPage } from "./features/organizer/OrganizerProtocolsPage";
import { OrganizerAudiencePage } from "./features/organizer/OrganizerAudiencePage";
import { OrganizerBenchPage } from "./features/organizer/OrganizerBenchPage";
import { OrganizerIndexPage } from "./features/organizer/OrganizerIndexPage";
import { OrganizerMilestonesPage } from "./features/organizer/OrganizerMilestonesPage";
import { OrganizerNewcomersPage } from "./features/organizer/OrganizerNewcomersPage";
import { OrganizerPostPage } from "./features/organizer/OrganizerPostPage";
import { OrganizerTeamPage } from "./features/organizer/OrganizerTeamPage";
import { OrganizerLocationHubPage } from "./features/organizer/OrganizerLocationHubPage";
import { OrganizerLocationPage } from "./features/organizer/OrganizerLocationPage";
import { LocationRecordsRatingPage } from "./features/leaderboards/LocationRecordsRatingPage";
import { QueuePage } from "./features/queue/QueuePage";
import { SweepHqPage } from "./features/sweep_hq/SweepHqPage";
import { SweepWorldPage } from "./features/sweep_hq/SweepWorldPage";
import { NotFoundPage } from "./features/NotFoundPage";
import { TapTooltipLayer } from "./components/TapTooltipLayer";
import { useAppPath } from "./hooks/useAppPath";
import {
  RenderOgDefaultPage,
  RenderOgLocationPage,
  RenderOgUserPage,
} from "./features/sharing/RenderOgPage";
import { ShareSheetProvider } from "./features/sharing/ShareSheetContext";
import { TeaserClaimRunner } from "./features/portal/teaserClaim";
import { reportAuthDoneOnce } from "./lib/abTest";
import { getCurrentUser } from "./lib/api";
import { useOptionalUser } from "./lib/useOptionalUser";
import { startPageView } from "./lib/pageAnalytics";
import { applyPageMeta, isLocationEntityPath, resolvePageMeta } from "./lib/pageMeta";
import { deferMetrikaHit, reportMetrikaHit } from "./lib/metrika";
import { isLegacyGrafanaPath, legacyGrafanaHref } from "./lib/siteBrand";
import { buildVisitorKey } from "./lib/siteVisitor";

function useSitePageviewTracking(path: string) {
  useEffect(() => {
    // Служебный рендер OG-картинок открывает Playwright — это не визиты людей,
    // в аналитику им нельзя.
    if (path.startsWith("/render/")) {
      return;
    }
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
        // Ступень воронки: вход завершён. Раз на пару (браузер, пользователь) —
        // когорту new/returning ставит сервер по возрасту аккаунта.
        reportAuthDoneOnce(user.id);
      })
      .catch(() => begin(false, undefined));
    return () => {
      cancelled = true;
      cleanup?.();
    };
  }, [path]);
}

/**
 * Заголовок вкладки и мета-теги по адресу. Страницы с сущностью (локация)
 * уточняют их у себя, когда данные загрузятся, — здесь ставится родовой
 * вариант, чтобы вкладка не оставалась с заголовком предыдущей страницы.
 */
function usePageMeta(path: string) {
  useEffect(() => {
    applyPageMeta(resolvePageMeta(path));
    // Метрика в SPA сама переходы не видит — репортим здесь же, где меняется
    // заголовок вкладки. Страницы-сущности досылают хит сами после данных,
    // чтобы в отчёт ушло «5 вёрст Бутово…», а не родовое «Локация».
    if (isLocationEntityPath(path)) {
      deferMetrikaHit(path);
    } else {
      reportMetrikaHit(path);
    }
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
      // Якорь переезжает вместе с адресом: ссылки вида /dashboard#profiles
      // должны докручивать до секции и после редиректа.
      window.location.replace(target + window.location.hash);
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
  // История релизов сайта — публичная, ссылки в футере (раздел + номер версии).
  [PORTAL_UPDATES_HREF]: () => <PortalUpdatesPage />,
  "/new/map-lab": () => <PortalMapLab />,
  // Личный кабинет в портальном дизайне — тёмный запуск под /new/*, рядом со
  // старым кабинетом на канонических адресах. Превью на демо-данных (без
  // логина) — для выбора вариантов дизайна; удалить вместе с /new/* при релизе.
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
  // Онбординг первичного входа: поиск себя по ФИО и привязка профилей.
  "/welcome": () => <OnboardingPage />,
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
  // Посадочная под «5 вёрст результаты»: последний старт каждой площадки.
  "/results": () => <LastResultsPage />,
  // Единый протокол недели: все площадки всех систем в порядке финиша.
  // Без даты — последняя неделя с данными.
  "/protocol": () => <UnifiedProtocolPage saturday={null} />,
  "/history": () => <CabinetLegacyRedirect tab="history" />,
  // Рейтинги открыты без логина (решение 25.07.2026): аноним видит таблицы,
  // а свою строку и позицию — только залогиненный (баннер-призыв на страницах).
  "/ratings": () => <LeaderboardsHubPage />,
  "/ratings/runs": () => <LeaderboardPage metric="runs" />,
  "/ratings/volunteering": () => <LeaderboardPage metric="volunteering" />,
  "/ratings/volunteer-roles": () => <LeaderboardPage metric="volunteer_roles" />,
  "/ratings/locations": () => <LeaderboardPage metric="locations" />,
  "/ratings/volunteer-locations": () => <LeaderboardPage metric="volunteer_locations" />,
  "/ratings/openings": () => <LeaderboardPage metric="openings" />,
  "/ratings/wins": () => <LeaderboardPage metric="wins" />,
  "/ratings/fastest": () => <FastestRatingPage />,
  "/ratings/win-locations": () => <LeaderboardPage metric="win_locations" />,
  "/ratings/home-distance": () => <LeaderboardPage metric="home_distance" />,
  "/ratings/location-records": () => <LocationRecordsRatingPage />,
  // Просмотр открыт всем; писать (карточка/голос/комментарий) может только
  // залогиненный — гейт внутри самой страницы, как у /locations.
  "/backlog": () => <BacklogPage />,
  // Кабинет организатора: список доступных локаций; сами кабинеты — по
  // /organizer/{slug} (regex-ветки в renderRoute). Доступ проверяет бэкенд.
  "/organizer": () => <OrganizerIndexPage />,

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
  "/admin/location-openings": () => <AdminLocationOpeningsPage />,
  "/admin/blog": () => <AdminBlogPage />,
  "/admin/releases": () => <AdminReleasesPage />,
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

/**
 * Настоящая ли это дата. Форму («2026-08-99») ловит регулярка адреса, а вот
 * 30 февраля она пропускает — и Date.parse тоже: JS молча переносит такую дату
 * на 2 марта. Поэтому сверяем разбор с исходной строкой: несуществующая дата
 * должна уходить в 404 вместе с бэкендом (см. is_known_path в seo_service),
 * а не в ошибку API.
 */
function isRealDate(iso: string): boolean {
  const parsed = new Date(`${iso}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === iso;
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
  const organizerAbsenceMatch = path.match(/^\/organizer\/([^/]+)\/absence$/);
  if (organizerAbsenceMatch) {
    return <OrganizerAbsencePage slug={decodeURIComponent(organizerAbsenceMatch[1])} />;
  }
  const organizerReportMatch = path.match(/^\/organizer\/([^/]+)\/report$/);
  if (organizerReportMatch) {
    return <OrganizerLocationPage slug={decodeURIComponent(organizerReportMatch[1])} />;
  }
  const organizerPostMatch = path.match(/^\/organizer\/([^/]+)\/post$/);
  if (organizerPostMatch) {
    return <OrganizerPostPage slug={decodeURIComponent(organizerPostMatch[1])} />;
  }
  const organizerMilestonesMatch = path.match(/^\/organizer\/([^/]+)\/milestones$/);
  if (organizerMilestonesMatch) {
    return <OrganizerMilestonesPage slug={decodeURIComponent(organizerMilestonesMatch[1])} />;
  }
  const organizerNewcomersMatch = path.match(/^\/organizer\/([^/]+)\/newcomers$/);
  if (organizerNewcomersMatch) {
    return <OrganizerNewcomersPage slug={decodeURIComponent(organizerNewcomersMatch[1])} />;
  }
  const organizerBenchMatch = path.match(/^\/organizer\/([^/]+)\/volunteers$/);
  if (organizerBenchMatch) {
    return <OrganizerBenchPage slug={decodeURIComponent(organizerBenchMatch[1])} />;
  }
  const organizerTeamMatch = path.match(/^\/organizer\/([^/]+)\/team$/);
  if (organizerTeamMatch) {
    return <OrganizerTeamPage slug={decodeURIComponent(organizerTeamMatch[1])} />;
  }
  const organizerAttendanceMatch = path.match(/^\/organizer\/([^/]+)\/attendance$/);
  if (organizerAttendanceMatch) {
    return <OrganizerAttendancePage slug={decodeURIComponent(organizerAttendanceMatch[1])} />;
  }
  const organizerAudienceMatch = path.match(/^\/organizer\/([^/]+)\/audience$/);
  if (organizerAudienceMatch) {
    return <OrganizerAudiencePage slug={decodeURIComponent(organizerAudienceMatch[1])} />;
  }
  const organizerProtocolsMatch = path.match(/^\/organizer\/([^/]+)\/protocols$/);
  if (organizerProtocolsMatch) {
    return <OrganizerProtocolsPage slug={decodeURIComponent(organizerProtocolsMatch[1])} />;
  }
  // Вход в кабинет локации — хаб с выбором инструмента, а не сразу таблица.
  const organizerLocationMatch = path.match(/^\/organizer\/([^/]+)$/);
  if (organizerLocationMatch) {
    return <OrganizerLocationHubPage slug={decodeURIComponent(organizerLocationMatch[1])} />;
  }
  // Протокол одного старта: /locations/{slug}/protocol/{система}/{дата}.
  // Единый протокол конкретной недели: /protocol/{дата}. В адресе — суббота,
  // но бэкенд принимает любой день недели и сам приводит к субботе.
  const unifiedProtocolMatch = path.match(/^\/protocol\/(\d{4}-\d{2}-\d{2})$/);
  if (unifiedProtocolMatch && isRealDate(unifiedProtocolMatch[1])) {
    return <UnifiedProtocolPage saturday={unifiedProtocolMatch[1]} />;
  }
  const locationProtocolMatch = path.match(
    /^\/locations\/([^/]+)\/protocol\/([^/]+)\/(\d{4}-\d{2}-\d{2})$/,
  );
  if (locationProtocolMatch) {
    return (
      <LocationProtocolPage
        slug={decodeURIComponent(locationProtocolMatch[1])}
        platformCode={decodeURIComponent(locationProtocolMatch[2])}
        eventDate={locationProtocolMatch[3]}
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
  // Служебный рендер OG-картинок: открывает Playwright из celery-задачи
  // og_render (снаружи путь закрыт в host-nginx). См. features/sharing/RenderOgPage.
  const renderOgLocationMatch = path.match(/^\/render\/og\/location\/([^/]+)$/);
  if (renderOgLocationMatch) {
    return <RenderOgLocationPage slug={decodeURIComponent(renderOgLocationMatch[1])} />;
  }
  const renderOgUserMatch = path.match(/^\/render\/og\/user\/([^/]+)$/);
  if (renderOgUserMatch) {
    return <RenderOgUserPage handle={decodeURIComponent(renderOgUserMatch[1])} />;
  }
  if (path === "/render/og/default") {
    return <RenderOgDefaultPage />;
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
  usePageMeta(path);
  // Отложенная привязка из тизера главной: сработает на любой странице, куда
  // провайдер вернул человека после входа, поэтому живёт на уровне App.
  const viewer = useOptionalUser();
  // Шторка «Поделиться» доступна из любого раздела — провайдер на всё дерево.
  return (
    <ShareSheetProvider>
      {renderRoute(path)}
      <TeaserClaimRunner userId={viewer?.id ?? null} />
      {/* Тап-подсказки на телефоне — один слой на весь сайт (см. TapTooltipLayer). */}
      <TapTooltipLayer />
    </ShareSheetProvider>
  );
}
