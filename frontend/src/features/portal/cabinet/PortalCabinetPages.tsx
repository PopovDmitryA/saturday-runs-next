/**
 * Страницы личного кабинета в портальном дизайне (тёмный запуск: /new/*).
 * Контент вкладок переиспользуется из существующих страниц в bare-режиме,
 * каркас с сайдбаром и портальный рескин даёт PortalCabinetShell + cabinet.css.
 */
import { RequireAuth } from "../../../components/RequireAuth";
import { AchievementsContent } from "../../achievements/AchievementsPage";
import { CoRunnersContent } from "../../co_runners/CoRunnersPage";
import { HistoryContent, useOwnSiteUrl } from "../../history/HistoryPage";
import { MapsContent } from "../../maps/MapsPage";
import { RunsContent } from "../../runs/RunsPage";
import { SettingsContent } from "../../settings/SettingsPage";
import { SharingContent } from "../../sharing/SharingGalleryPage";
import { VolunteeringContent } from "../../volunteering/VolunteeringPage";
import { getCoRunnerMeetings, getCoRunners, getMyHistory, type User } from "../../../lib/api";
import { PORTAL_CABINET_SHARE_HREF, PORTAL_LOGIN_HREF } from "../../../lib/portalRoutes";
import { PortalCabinetShell, type CabinetTabKey } from "./PortalCabinetShell";

function cabinetPage(
  active: CabinetTabKey,
  // Заголовок каркаса; undefined — когда контент вкладки рисует свой (интро-карточка).
  title: string | undefined,
  sub: string | undefined,
  renderContent: (user: User) => React.ReactNode,
) {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {(user) => (
        <PortalCabinetShell active={active} user={user} title={title} sub={sub}>
          {renderContent(user)}
        </PortalCabinetShell>
      )}
    </RequireAuth>
  );
}

export function PortalCabinetRunsPage() {
  return cabinetPage(
    "runs",
    "Пробежки",
    "Все финиши по всем привязанным системам — с фильтрами, сортировкой и оценками стартов.",
    () => <RunsContent bare />,
  );
}

export function PortalCabinetVolunteeringPage() {
  return cabinetPage(
    "volunteering",
    "Волонтёрство",
    "Все волонтёрские позиции по всем привязанным системам.",
    () => <VolunteeringContent bare />,
  );
}

export function PortalCabinetAchievementsPage() {
  return cabinetPage(
    "achievements",
    "Цели и достижения",
    "Медали, челленджи, клубы и личные цели на год.",
    () => <AchievementsContent bare />,
  );
}

// Обёртки на уровне модуля: CoRunnersContent держит load в зависимостях эффекта,
// поэтому ссылки на функции должны быть неизменными между рендерами.
const loadMyCoRunners = (platforms: string[]) => getCoRunners(100, platforms);
const loadMyCoRunnerMeetings = (participantKey: string, platforms: string[]) =>
  getCoRunnerMeetings(participantKey, platforms);

export function PortalCabinetMeetingsPage() {
  // Заголовок рисует сам CoRunnersContent (интро-карточка «Встречи на стартах»).
  return cabinetPage("meetings", undefined, undefined, () => (
    <CoRunnersContent load={loadMyCoRunners} loadMeetings={loadMyCoRunnerMeetings} />
  ));
}

export function PortalCabinetMapPage() {
  return cabinetPage(
    "map",
    "Карта",
    "Посещённые локации и регионы — по пробежкам и волонтёрствам.",
    () => <MapsContent bare />,
  );
}

function PortalHistoryBody() {
  const siteUrl = useOwnSiteUrl();
  return <HistoryContent load={getMyHistory} shareBase={PORTAL_CABINET_SHARE_HREF} siteUrl={siteUrl} />;
}

export function PortalCabinetHistoryPage() {
  // Заголовок рисует сам HistoryContent (интро-карточка со сводкой).
  return cabinetPage("history", undefined, undefined, () => <PortalHistoryBody />);
}

export function PortalCabinetSharePage() {
  return cabinetPage(
    "share",
    "Поделиться",
    "Готовые сюжеты из вашей статистики: постер для сториз или чата — в один тап.",
    () => <SharingContent bare />,
  );
}

export function PortalCabinetSettingsPage() {
  return cabinetPage(
    "settings",
    "Настройки",
    "Приватность, привязка профилей, домашняя локация и способы входа.",
    () => <SettingsContent bare />,
  );
}
