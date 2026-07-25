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

  // Чужой профиль: вкладки внутри страницы переключаются без смены адреса,
  // поэтому лишний сегмент здесь — не наш адрес.
  if (segment) {
    return <NotFoundPage />;
  }
  return <PublicProfilePage handle={handle} />;
}
