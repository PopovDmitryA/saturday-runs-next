/**
 * Один адрес — два режима (решение Дмитрия 26.07.2026).
 *
 * `/users/{хендл}` и `/users/{хендл}/{вкладка}`: если хендл принадлежит
 * текущему пользователю — открывается полноценный личный кабинет, если чужой
 * или гость — публичный профиль участника. Смысл в том, чтобы бегун видел в
 * адресной строке ссылку, которой можно поделиться, вместо служебного адреса
 * кабинета, а публичные страницы сайта имели «человеческие» адреса.
 */
import { PublicProfilePage } from "../public_profile/PublicProfilePage";
import { PortalCabinetDashboardPage } from "../portal/cabinet/PortalCabinetDashboardPage";
import {
  PortalCabinetAchievementsPage,
  PortalCabinetHistoryPage,
  PortalCabinetMapPage,
  PortalCabinetMeetingsPage,
  PortalCabinetRunsPage,
  PortalCabinetVolunteeringPage,
} from "../portal/cabinet/PortalCabinetPages";
import { NotFoundPage } from "../NotFoundPage";
import { CABINET_TAB_SEGMENTS, isOwnHandle, type CabinetTabSegmentKey } from "../../lib/portalRoutes";
import { useOptionalUser } from "../../lib/useOptionalUser";

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

  // Чужой профиль: вкладки внутри страницы переключаются без смены адреса,
  // поэтому лишний сегмент здесь — не наш адрес.
  if (segment) {
    return <NotFoundPage />;
  }
  return <PublicProfilePage handle={handle} />;
}
