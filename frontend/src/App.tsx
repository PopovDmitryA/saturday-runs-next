import type { ReactElement } from "react";
import { Fragment, Suspense, useEffect } from "react";
import { ProfileRoute } from "./features/profile/ProfileRoute";
import { OAuthCallbackPage } from "./features/auth/OAuthCallbackPage";
import { TelegramReturnPage } from "./features/auth/TelegramReturnPage";
import { PortalAboutPage } from "./features/portal/PortalAboutPage";
import { PortalBlogPage } from "./features/portal/PortalBlogPage";
import { PortalHomePage } from "./features/portal/PortalHomePage";
import { PortalLoginPage } from "./features/portal/PortalLoginPage";
import { PortalUpdatesPage } from "./features/portal/PortalUpdatesPage";
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
import { NotFoundPage } from "./features/NotFoundPage";
import { TapTooltipLayer } from "./components/TapTooltipLayer";
import { SiteSearchDialog } from "./features/portal/nav/SiteSearchDialog";
import { useEntryKey } from "./hooks/useEntryKey";
import { useAppPath } from "./hooks/useAppPath";
import { ShareSheetProvider } from "./features/sharing/ShareSheetContext";
import { TeaserClaimRunner } from "./features/portal/teaserClaim";
import { reportAuthDoneOnce } from "./lib/abTest";
import { getCurrentUser } from "./lib/api";
import { useOptionalUser } from "./lib/useOptionalUser";
import { startPageView } from "./lib/pageAnalytics";
import { applyPageMeta, isLocationEntityPath, resolvePageMeta } from "./lib/pageMeta";
import { deferMetrikaHit, reportMetrikaHit } from "./lib/metrika";
import { isLegacyGrafanaPath, legacyGrafanaTarget } from "./lib/siteBrand";
import { buildVisitorKey } from "./lib/siteVisitor";
import { LazyErrorBoundary, RouteFallback, lazyPage } from "./lib/lazyPage";

// Разделы, не нужные на первом экране, грузятся по первому обращению (см.
// lib/lazyPage): админка, кабинет организатора, локации и протоколы, рейтинги,
// постеры, /hq, бэклог, онбординг. Главная, вход, кабинет участника и сайдбар
// остаются в стартовом чанке — их видит каждый.
const LocationTopsPage = lazyPage(() => import("./features/locations/LocationTopsPage"), (m) => m.LocationTopsPage);
const LocationWeatherPage = lazyPage(() => import("./features/locations/LocationWeatherPage"), (m) => m.LocationWeatherPage);
const OrganizerBenchmarkPage = lazyPage(() => import("./features/organizer/OrganizerBenchmarkPage"), (m) => m.OrganizerBenchmarkPage);
const AdminAbusePage = lazyPage(() => import("./features/admin/AdminAbusePage"), (m) => m.AdminAbusePage);
const AdminBlockedSlugsPage = lazyPage(() => import("./features/admin/AdminBlockedSlugsPage"), (m) => m.AdminBlockedSlugsPage);
const AdminStatsPage = lazyPage(() => import("./features/admin/AdminStatsPage"), (m) => m.AdminStatsPage);
const AdminSyncRunsPage = lazyPage(() => import("./features/admin/AdminSyncRunsPage"), (m) => m.AdminSyncRunsPage);
const AdminPageAnalyticsPage = lazyPage(() => import("./features/admin/AdminPageAnalyticsPage"), (m) => m.AdminPageAnalyticsPage);
const AdminSearchLogPage = lazyPage(() => import("./features/admin/AdminSearchLogPage"), (m) => m.AdminSearchLogPage);
const AdminRatingsPage = lazyPage(() => import("./features/admin/AdminRatingsPage"), (m) => m.AdminRatingsPage);
const AdminResyncPage = lazyPage(() => import("./features/admin/AdminResyncPage"), (m) => m.AdminResyncPage);
const AdminLocationContactsPage = lazyPage(() => import("./features/admin/AdminLocationContactsPage"), (m) => m.AdminLocationContactsPage);
const AdminLocationOpeningsPage = lazyPage(() => import("./features/admin/AdminLocationOpeningsPage"), (m) => m.AdminLocationOpeningsPage);
const AdminRecordsDigestPage = lazyPage(() => import("./features/admin/AdminRecordsDigestPage"), (m) => m.AdminRecordsDigestPage);
const AdminUsersPage = lazyPage(() => import("./features/admin/AdminUsersPage"), (m) => m.AdminUsersPage);
const OnboardingPage = lazyPage(() => import("./features/onboarding/OnboardingPage"), (m) => m.OnboardingPage);
const PortalMapLab = lazyPage(() => import("./features/portal/PortalMapLab"), (m) => m.PortalMapLab);
const AdminBlogPage = lazyPage(() => import("./features/admin/AdminBlogPage"), (m) => m.AdminBlogPage);
const AdminReleasesPage = lazyPage(() => import("./features/admin/AdminReleasesPage"), (m) => m.AdminReleasesPage);
const AdminBacklogPage = lazyPage(() => import("./features/admin/AdminBacklogPage"), (m) => m.AdminBacklogPage);
const AdminNotificationsPage = lazyPage(
  () => import("./features/admin/AdminNotificationsPage"),
  (m) => m.AdminNotificationsPage,
);
const BacklogPage = lazyPage(() => import("./features/backlog/BacklogPage"), (m) => m.BacklogPage);
const AdminTrackImportsPage = lazyPage(() => import("./features/admin/AdminTrackImportsPage"), (m) => m.AdminTrackImportsPage);
const CourseRatingPage = lazyPage(() => import("./features/leaderboards/CourseRatingPage"), (m) => m.CourseRatingPage);
const LocationEventsPage = lazyPage(() => import("./features/locations/LocationEventsPage"), (m) => m.LocationEventsPage);
const LocationParticipantsPage = lazyPage(() => import("./features/locations/LocationParticipantsPage"), (m) => m.LocationParticipantsPage);
const LocationProtocolPage = lazyPage(() => import("./features/locations/LocationProtocolPage"), (m) => m.LocationProtocolPage);
const LocationPage = lazyPage(() => import("./features/locations/LocationPage"), (m) => m.LocationPage);
const LastResultsPage = lazyPage(() => import("./features/locations/LastResultsPage"), (m) => m.LastResultsPage);
const LocationsIndexPage = lazyPage(() => import("./features/locations/LocationsIndexPage"), (m) => m.LocationsIndexPage);
const FastestRatingPage = lazyPage(() => import("./features/leaderboards/FastestRatingPage"), (m) => m.FastestRatingPage);
const UnifiedProtocolPage = lazyPage(() => import("./features/locations/UnifiedProtocolPage"), (m) => m.UnifiedProtocolPage);
const LeaderboardPage = lazyPage(() => import("./features/leaderboards/LeaderboardPage"), (m) => m.LeaderboardPage);
const LeaderboardsHubPage = lazyPage(() => import("./features/leaderboards/LeaderboardsHubPage"), (m) => m.LeaderboardsHubPage);
const OrganizerAbsencePage = lazyPage(() => import("./features/organizer/OrganizerAbsencePage"), (m) => m.OrganizerAbsencePage);
const OrganizerAttendancePage = lazyPage(() => import("./features/organizer/OrganizerAttendancePage"), (m) => m.OrganizerAttendancePage);
const OrganizerProtocolsPage = lazyPage(() => import("./features/organizer/OrganizerProtocolsPage"), (m) => m.OrganizerProtocolsPage);
const OrganizerAudiencePage = lazyPage(() => import("./features/organizer/OrganizerAudiencePage"), (m) => m.OrganizerAudiencePage);
const OrganizerBenchPage = lazyPage(() => import("./features/organizer/OrganizerBenchPage"), (m) => m.OrganizerBenchPage);
const OrganizerIndexPage = lazyPage(() => import("./features/organizer/OrganizerIndexPage"), (m) => m.OrganizerIndexPage);
const OrganizerMilestonesPage = lazyPage(() => import("./features/organizer/OrganizerMilestonesPage"), (m) => m.OrganizerMilestonesPage);
const OrganizerNewcomersPage = lazyPage(() => import("./features/organizer/OrganizerNewcomersPage"), (m) => m.OrganizerNewcomersPage);
const OrganizerPostPage = lazyPage(() => import("./features/organizer/OrganizerPostPage"), (m) => m.OrganizerPostPage);
const OrganizerTeamPage = lazyPage(() => import("./features/organizer/OrganizerTeamPage"), (m) => m.OrganizerTeamPage);
const OrganizerLocationHubPage = lazyPage(() => import("./features/organizer/OrganizerLocationHubPage"), (m) => m.OrganizerLocationHubPage);
const OrganizerLocationPage = lazyPage(() => import("./features/organizer/OrganizerLocationPage"), (m) => m.OrganizerLocationPage);
const LocationRecordsRatingPage = lazyPage(() => import("./features/leaderboards/LocationRecordsRatingPage"), (m) => m.LocationRecordsRatingPage);
const RegionsRatingPage = lazyPage(() => import("./features/leaderboards/RegionsRatingPage"), (m) => m.RegionsRatingPage);
const QueuePage = lazyPage(() => import("./features/queue/QueuePage"), (m) => m.QueuePage);
const PortalCabinetSettingsPage = lazyPage(() => import("./features/portal/cabinet/PortalCabinetPages"), (m) => m.PortalCabinetSettingsPage);
const PortalCabinetSharePage = lazyPage(() => import("./features/portal/cabinet/PortalCabinetPages"), (m) => m.PortalCabinetSharePage);
const RenderOgDefaultPage = lazyPage(() => import("./features/sharing/RenderOgPage"), (m) => m.RenderOgDefaultPage);
const RenderOgLocationPage = lazyPage(() => import("./features/sharing/RenderOgPage"), (m) => m.RenderOgLocationPage);
const RenderOgUserPage = lazyPage(() => import("./features/sharing/RenderOgPage"), (m) => m.RenderOgUserPage);

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
  // Telegram возвращает данные во фрагменте адреса — разбирает их страница.
  "/auth/telegram/return": () => <TelegramReturnPage />,
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
  "/ratings/regions": () => <RegionsRatingPage />,
  "/ratings/courses": () => <CourseRatingPage />,
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
  // Журнал поиска по сайту: что ищут и что не находится.
  "/admin/search": () => <AdminSearchLogPage />,
  "/admin/ratings": () => <AdminRatingsPage />,
  "/admin/resync": () => <AdminResyncPage />,
  "/admin/records-digest": () => <AdminRecordsDigestPage />,
  "/admin/location-contacts": () => <AdminLocationContactsPage />,
  "/admin/location-openings": () => <AdminLocationOpeningsPage />,
  "/admin/blog": () => <AdminBlogPage />,
  "/admin/releases": () => <AdminReleasesPage />,
  "/admin/backlog": () => <AdminBacklogPage />,
  "/admin/notifications": () => <AdminNotificationsPage />,
  "/admin/track-imports": () => <AdminTrackImportsPage />,

};

/**
 * Старые адреса дашбордов (/d/<uid>/…) достались сайту от Grafana, которая
 * жила на этом же домене. Пока она работала — отсюда уводили на неё; теперь
 * grafana.run5k.run закрыт, поэтому известный дашборд открывает свою страницу
 * на сайте, а незнакомый уходит в NotFoundPage с объяснением (иначе получалось
 * кольцо «сайт → заглушка Grafana → сайт»).
 */
function LegacyGrafanaRedirect() {
  const target = legacyGrafanaTarget(window.location.pathname);
  useEffect(() => {
    if (target) {
      window.location.replace(target);
    }
  }, [target]);
  if (!target) {
    return <NotFoundPage />;
  }
  return (
    <main className="app">
      <p className="muted">Этот дашборд переехал на сайт, открываем…</p>
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
  const organizerBenchmarkMatch = path.match(/^\/organizer\/([^/]+)\/benchmark$/);
  if (organizerBenchmarkMatch) {
    return <OrganizerBenchmarkPage slug={decodeURIComponent(organizerBenchmarkMatch[1])} />;
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
  // Постоянный состав локации: развёрнутый топ со страницы площадки.
  const locationParticipantsMatch = path.match(/^\/locations\/([^/]+)\/participants$/);
  if (locationParticipantsMatch) {
    return <LocationParticipantsPage slug={decodeURIComponent(locationParticipantsMatch[1])} />;
  }
  // Полные топы бегунов локации: на самой странице локации от каждого видна
  // только пятёрка.
  const locationTopsMatch = path.match(/^\/locations\/([^/]+)\/tops$/);
  if (locationTopsMatch) {
    return <LocationTopsPage slug={decodeURIComponent(locationTopsMatch[1])} />;
  }
  // Погода на стартах локации: графики по месяцам, рекорды, явка по погоде.
  const locationWeatherMatch = path.match(/^\/locations\/([^/]+)\/weather$/);
  if (locationWeatherMatch) {
    return <LocationWeatherPage slug={decodeURIComponent(locationWeatherMatch[1])} />;
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
  // Ключ записи истории пересобирает страницу на каждом переходе: одна и та же
  // страница на соседнем адресе (локация → другая локация, профиль → профиль)
  // иначе переиспользовала бы экземпляр, и на новый адрес утекало бы состояние
  // прежнего, а на «назад» не приезжал бы снимок записи (см. hooks/useEntryKey).
  // replaceState ключ не меняет — правка адреса под фильтры страницу не трогает.
  const entryKey = useEntryKey();
  return (
    <ShareSheetProvider>
      <Fragment key={entryKey}>
        <LazyErrorBoundary>
          <Suspense fallback={<RouteFallback />}>{renderRoute(path)}</Suspense>
        </LazyErrorBoundary>
      </Fragment>
      <TeaserClaimRunner userId={viewer?.id ?? null} />
      {/* Тап-подсказки на телефоне — один слой на весь сайт (см. TapTooltipLayer). */}
      <TapTooltipLayer />
      {/* Поиск по сайту — одно окно на всё приложение, открывается из шапки,
          рельса, «Меню» и по ⌘K / «/» (см. nav/SiteSearchDialog). */}
      <SiteSearchDialog />
    </ShareSheetProvider>
  );
}
