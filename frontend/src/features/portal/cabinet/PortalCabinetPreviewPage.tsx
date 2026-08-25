/**
 * DEV-ONLY превью портального ЛК на демо-данных (без авторизации).
 * Регистрируется в App.tsx только при import.meta.env.DEV — в прод-сборку
 * не попадает. Нужна, чтобы смотреть вёрстку каркаса и вкладок локально,
 * не имея живой сессии (OAuth на dev-порту недоступен).
 *
 * ?tab=runs|volunteering|meetings|history|map — какая вкладка открыта
 * (по умолчанию дашборд). Достижений здесь нет: у них нет демо-данных.
 */
import { useCallback, useEffect, useState } from "react";
import { DashboardAnalytics } from "../../../components/DashboardAnalytics";
import { MyHistoryTeaser } from "../../../components/MyHistoryTeaser";
import { OnThisDayCard } from "../../../components/OnThisDayCard";
import { CoRunnersContent } from "../../co_runners/CoRunnersPage";
import { HistoryContent } from "../../history/HistoryPage";
import { MapsContent } from "../../maps/MapsPage";
import { RunsContent } from "../../runs/RunsPage";
import { VolunteeringContent } from "../../volunteering/VolunteeringPage";
import { AppDataSourceProvider, demoDataSource } from "../../../lib/appDataSource";
import {
  demoGetCoRunnerMeetings,
  demoGetCoRunners,
  demoGetMyHistory,
  demoGetOnThisDay,
  getDemoDashboard,
  type AdminUserPreviewDashboard,
  type User,
} from "../../../lib/api";
import { PORTAL_CABINET_ACHIEVEMENTS_HREF } from "../../../lib/portalRoutes";
import { DashboardHero } from "./PortalCabinetDashboardPage";
import { PortalCabinetShell, type CabinetTabKey } from "./PortalCabinetShell";

const PREVIEW_USER: User = {
  id: "00000000-0000-0000-0000-000000000000",
  telegram_id: null,
  telegram_username: null,
  telegram_first_name: null,
  telegram_last_name: null,
  display_name: "Демо-участник",
  display_name_style: "auto",
  onboarding_no_account_platforms: [],
  display_name_notice: null,
  display_name_suggestion: null,
  consent_accepted: true,
  is_admin: false,
  avatar_url: null,
  avatar_full_url: null,
  serial_id: null,
  public_slug: null,
  auth_identities: [],
};

function PreviewDashboard() {
  const [data, setData] = useState<AdminUserPreviewDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await getDemoDashboard());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить демо-профиль");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <div className="card error">
        <p>{error}</p>
      </div>
    );
  }
  if (!data) {
    return <p className="muted">Загрузка…</p>;
  }

  const stats = data.stats;

  return (
    <div className="portal-cab-stack">
      <DashboardHero
        data={data}
        userName="Демо-участник"
        hrefForStat={(key) => `${PREVIEW_PATH}?tab=${key === "vol" ? "volunteering" : key === "geo" ? "map" : key}`}
      />

      <OnThisDayCard load={demoGetOnThisDay} />

      <MyHistoryTeaser load={demoGetMyHistory} href="#" />

      <DashboardAnalytics
        analytics={stats.analytics}
        totalRuns={stats.total_runs}
        totalVolunteering={stats.total_volunteering}
      />
    </div>
  );
}

const PREVIEW_PATH = "/new/cabinet-preview";

function previewTab(): CabinetTabKey {
  const tab = new URLSearchParams(window.location.search).get("tab");
  const known: CabinetTabKey[] = [
    "runs",
    "volunteering",
    "achievements",
    "meetings",
    "history",
    "map",
    "share",
  ];
  return known.includes(tab as CabinetTabKey) ? (tab as CabinetTabKey) : "dashboard";
}

export function PortalCabinetPreviewPage() {
  const tab = previewTab();

  let title: string | undefined;
  let sub: string | undefined;
  let content: React.ReactNode;
  switch (tab) {
    case "runs":
      title = "Пробежки";
      sub = "Все финиши по всем привязанным системам — с фильтрами, сортировкой и оценками стартов.";
      content = <RunsContent bare />;
      break;
    case "volunteering":
      title = "Волонтёрство";
      sub = "Все волонтёрские позиции по всем привязанным системам.";
      content = <VolunteeringContent bare />;
      break;
    case "achievements":
      title = "Цели и достижения";
      content = (
        <div className="card">
          <p className="muted">
            У раздела нет демо-данных — вёрстку видно только под своей учётной записью, по
            адресу {PORTAL_CABINET_ACHIEVEMENTS_HREF}.
          </p>
        </div>
      );
      break;
    case "meetings":
      content = <CoRunnersContent load={demoGetCoRunners} loadMeetings={demoGetCoRunnerMeetings} />;
      break;
    case "share":
      title = "Поделиться";
      content = (
        <div className="card">
          <p className="muted">
            У раздела нет демо-данных — карточки собираются из вашей личной статистики, вёрстку
            видно только под своей учётной записью.
          </p>
        </div>
      );
      break;
    case "history":
      content = (
        <HistoryContent
          load={demoGetMyHistory}
          shareBase="/share"
          siteUrl={window.location.origin}
        />
      );
      break;
    case "map":
      title = "Карта";
      sub = "Посещённые локации и регионы — по пробежкам и волонтёрствам.";
      content = <MapsContent bare />;
      break;
    default:
      content = <PreviewDashboard />;
  }

  return (
    <AppDataSourceProvider source={demoDataSource}>
      <PortalCabinetShell
        active={tab}
        user={PREVIEW_USER}
        title={title}
        sub={sub}
        // Внутри превью навигация остаётся в превью: реальные разделы под
        // RequireAuth, без сессии клик выбрасывал бы на страницу входа.
        hrefForTab={(key) => (key === "dashboard" ? PREVIEW_PATH : `${PREVIEW_PATH}?tab=${key}`)}
        hideSecondaryNav
      >
        {content}
      </PortalCabinetShell>
    </AppDataSourceProvider>
  );
}
