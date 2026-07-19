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
import { pluralFormRu } from "../../../lib/format";
import { formatHeroFinishTime } from "./PortalCabinetDashboardPage";
import { PortalCabinetShell, type CabinetTabKey } from "./PortalCabinetShell";

const PREVIEW_USER: User = {
  id: "00000000-0000-0000-0000-000000000000",
  telegram_id: null,
  telegram_username: null,
  telegram_first_name: null,
  telegram_last_name: null,
  display_name: "Демо-участник",
  display_name_customized: true,
  consent_accepted: true,
  is_admin: false,
  auth_identities: [],
};

const RUN_FORMS = ["пробежка", "пробежки", "пробежек"] as const;
const VOL_FORMS = ["волонтёрство", "волонтёрства", "волонтёрств"] as const;
const LOCATION_FORMS = ["локация", "локации", "локаций"] as const;

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
  const analytics = stats.analytics;

  return (
    <>
      <section className="portal-cab-hero">
        <h1 className="portal-cab-hero-title">Добрый день!</h1>
        <p className="portal-cab-hero-sub">Ваша сводная статистика по всем беговым системам.</p>
        <div className="portal-cab-hero-stats">
          <div className="portal-cab-hero-stat portal-cab-hero-stat-runs">
            <div className="portal-cab-hero-stat-value">{stats.total_runs}</div>
            <div className="portal-cab-hero-stat-label">
              {pluralFormRu(stats.total_runs, RUN_FORMS)}
            </div>
          </div>
          <div className="portal-cab-hero-stat portal-cab-hero-stat-vol">
            <div className="portal-cab-hero-stat-value">{stats.total_volunteering}</div>
            <div className="portal-cab-hero-stat-label">
              {pluralFormRu(stats.total_volunteering, VOL_FORMS)}
            </div>
          </div>
          <div className="portal-cab-hero-stat portal-cab-hero-stat-pr">
            <div className="portal-cab-hero-stat-value">
              {analytics.best_finish_time_sec != null
                ? formatHeroFinishTime(analytics.best_finish_time_sec)
                : "—"}
            </div>
            <div className="portal-cab-hero-stat-label">лучшее время на 5 км</div>
          </div>
          <div className="portal-cab-hero-stat portal-cab-hero-stat-geo">
            <div className="portal-cab-hero-stat-value">{analytics.unique_locations}</div>
            <div className="portal-cab-hero-stat-label">
              {pluralFormRu(analytics.unique_locations, LOCATION_FORMS)}
            </div>
          </div>
        </div>
      </section>

      <OnThisDayCard load={demoGetOnThisDay} shareBase="/share" />

      <MyHistoryTeaser load={demoGetMyHistory} href="#" />

      <DashboardAnalytics
        analytics={analytics}
        totalRuns={stats.total_runs}
        totalVolunteering={stats.total_volunteering}
      />
    </>
  );
}

function previewTab(): CabinetTabKey {
  const tab = new URLSearchParams(window.location.search).get("tab");
  const known: CabinetTabKey[] = ["runs", "volunteering", "meetings", "history", "map"];
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
    case "meetings":
      content = <CoRunnersContent load={demoGetCoRunners} loadMeetings={demoGetCoRunnerMeetings} />;
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
      <PortalCabinetShell active={tab} user={PREVIEW_USER} title={title} sub={sub}>
        {content}
      </PortalCabinetShell>
    </AppDataSourceProvider>
  );
}
