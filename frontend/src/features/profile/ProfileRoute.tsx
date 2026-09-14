/**
 * Один адрес — два режима (решение Дмитрия 26.07.2026).
 *
 * `/users/{хендл}` и `/users/{хендл}/{вкладка}`: если хендл принадлежит
 * текущему пользователю — открывается полноценный личный кабинет, если чужой
 * или гость — публичный профиль участника. Смысл в том, чтобы бегун видел в
 * адресной строке ссылку, которой можно поделиться, вместо служебного адреса
 * кабинета, а публичные страницы сайта имели «человеческие» адреса.
 */
import { useEffect } from "react";
import { PortalCabinetDashboardPage } from "../portal/cabinet/PortalCabinetDashboardPage";
import { NotFoundPage } from "../NotFoundPage";
import { lazyPage } from "../../lib/lazyPage";
import { CABINET_TAB_SEGMENTS, isOwnHandle, type CabinetTabSegmentKey } from "../../lib/portalRoutes";
import { useOptionalUser } from "../../lib/useOptionalUser";

// Сводка кабинета — первый экран залогиненного, она в стартовом чанке.
// Остальные вкладки (с картой на leaflet, ачивками, историей) и чужой профиль
// подгружаются по первому обращению; Suspense-фолбэк держит App.
const cabinetPages = () => import("../portal/cabinet/PortalCabinetPages");
const PublicProfilePage = lazyPage(() => import("../public_profile/PublicProfilePage"), (m) => m.PublicProfilePage);
const PortalCabinetRunsPage = lazyPage(cabinetPages, (m) => m.PortalCabinetRunsPage);
const PortalCabinetVolunteeringPage = lazyPage(cabinetPages, (m) => m.PortalCabinetVolunteeringPage);
const PortalCabinetAchievementsPage = lazyPage(cabinetPages, (m) => m.PortalCabinetAchievementsPage);
const PortalCabinetMeetingsPage = lazyPage(cabinetPages, (m) => m.PortalCabinetMeetingsPage);
const PortalCabinetMapPage = lazyPage(cabinetPages, (m) => m.PortalCabinetMapPage);
const PortalCabinetHistoryPage = lazyPage(cabinetPages, (m) => m.PortalCabinetHistoryPage);

/** Сегмент адреса → вкладка кабинета. */
const SEGMENT_TO_TAB = Object.fromEntries(
  Object.entries(CABINET_TAB_SEGMENTS).map(([tab, segment]) => [segment, tab as CabinetTabSegmentKey]),
) as Record<string, CabinetTabSegmentKey>;

const CABINET_PAGES: Record<CabinetTabSegmentKey, () => React.ReactElement> = {
  dashboard: () => <PortalCabinetDashboardPage />,
  runs: () => <PortalCabinetRunsPage />,
  volunteering: () => <PortalCabinetVolunteeringPage />,
  achievements: () => <PortalCabinetAchievementsPage />,
  meetings: () => <PortalCabinetMeetingsPage />,
  map: () => <PortalCabinetMapPage />,
  history: () => <PortalCabinetHistoryPage />,
};

export function ProfileRoute({ handle, segment }: { handle: string; segment?: string }) {
  const user = useOptionalUser();

  // Канонизация адреса: если у участника есть ник, показываем в строке
  // браузера /users/{ник}, даже когда пришли по числовому адресу (например,
  // из таблицы рейтинга, где ссылки строятся по номеру). replaceState —
  // без перезагрузки и без лишней записи в истории.
  useEffect(() => {
    const slug = user?.public_slug?.trim();
    if (!slug || !isOwnHandle(user, handle) || handle === slug) {
      return;
    }
    const suffix = segment ? `/${encodeURIComponent(segment)}` : "";
    window.history.replaceState(null, "", `/users/${encodeURIComponent(slug)}${suffix}`);
  }, [user, handle, segment]);

  // Пока сессия не подтверждена — ничего не рендерим: иначе свой же профиль
  // на мгновение показался бы гостевым (и наоборот).
  if (user === undefined) {
    return (
      <main className="app">
        <p className="muted">Загрузка…</p>
      </main>
    );
  }

  const own = isOwnHandle(user, handle);

  if (own) {
    const tab = segment ? SEGMENT_TO_TAB[segment] : "dashboard";
    if (!tab) {
      return <NotFoundPage />;
    }
    return CABINET_PAGES[tab]();
  }

  // Чужой профиль: сегмент — это вкладка. Адреса те же, что у своего кабинета,
  // поэтому ссылкой на чужую карту («/users/{хендл}/maps») можно делиться.
  const foreignTab = segment ? SEGMENT_TO_TAB[segment] : "dashboard";
  if (!foreignTab) {
    return <NotFoundPage />;
  }
  return <PublicProfilePage handle={handle} initialTab={foreignTab} />;
}
