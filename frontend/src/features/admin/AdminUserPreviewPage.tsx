import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { DashboardAnalytics } from "../../components/DashboardAnalytics";
import { DashboardStatCard } from "../../components/DashboardStatCard";
import { RequireAdmin } from "../../components/RequireAdmin";
import { AppDataSourceProvider, createAdminPreviewDataSource } from "../../lib/appDataSource";
import { AdminPreviewPlatformLinks } from "./AdminPreviewPlatformLinks";
import { AdminSubnav } from "./AdminSubnav";
import { UserMapPanel } from "../maps/UserMapPanel";
import { RunsContent } from "../runs/RunsPage";
import { VolunteeringContent } from "../volunteering/VolunteeringPage";
import {
  getAdminUserPreviewDashboard,
  getAdminUserPreviewVisitedMap,
  getAdminUserPreviewCatalogTable,
  getCatalogLocationsMap,
  type AdminUserPreviewDashboard,
} from "../../lib/api";
import { formatDateTime, runsCapLabel, volunteeringCapLabel } from "../../lib/format";
import { authProviderLabel, userLoginLines } from "./adminUserDisplay";

type PreviewTab = "dashboard" | "runs" | "volunteering" | "map";

function previewUserLabel(user: AdminUserPreviewDashboard["user"]): string {
  const logins = userLoginLines(user);
  if (logins.length > 0) {
    const first = logins[0];
    return `${authProviderLabel(first.provider)} — ${first.label}`;
  }
  if (user.display_name) {
    return user.display_name;
  }
  return `Пользователь ${user.id.slice(0, 8)}`;
}

function AdminUserPreviewContent({ userId }: { userId: string }) {
  const previewDataSource = useMemo(() => createAdminPreviewDataSource(userId), [userId]);
  const [tab, setTab] = useState<PreviewTab>("dashboard");
  const [dashboard, setDashboard] = useState<AdminUserPreviewDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadDashboard = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await getAdminUserPreviewDashboard(userId);
      setDashboard(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить профиль");
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    if (tab === "dashboard") void loadDashboard();
  }, [tab, loadDashboard]);

  const loadPreviewVisitedMap = useCallback(
    () => getAdminUserPreviewVisitedMap(userId, false),
    [userId],
  );
  const loadPreviewCatalogTable = useCallback(
    () => getAdminUserPreviewCatalogTable(userId, false),
    [userId],
  );

  const stats = dashboard?.stats;
  const byPlatform = (stats?.by_platform ?? {}) as Record<
    string,
    { runs?: number; volunteering?: number }
  >;

  const tabClass = (value: PreviewTab) =>
    tab === value ? "admin-preview-tab active" : "admin-preview-tab";

  return (
    <AppShell title="Просмотр пользователя" activePath="/admin">
      <AdminSubnav activePath="/admin/users" />

      <div className="banner admin-preview-banner">
        Режим просмотра: как пользователь видит личный кабинет. Изменения недоступны.
        <a href="/admin/users" className="admin-preview-back">
          ← К списку
        </a>
      </div>

      {dashboard && (
        <section className="card admin-preview-header">
          <h2 className="section-title">{previewUserLabel(dashboard.user)}</h2>
          <p className="muted">
            {userLoginLines(dashboard.user)
              .map((login) => `${authProviderLabel(login.provider)}: ${login.label}`)
              .join(" · ") || "Способ входа не указан"}
            {dashboard.computed_at && (
              <> · данные на {formatDateTime(String(dashboard.computed_at))}</>
            )}
          </p>
          <AdminPreviewPlatformLinks
            userId={userId}
            links={dashboard.platform_links}
            byPlatform={byPlatform}
            onSyncQueued={() => void loadDashboard()}
          />
        </section>
      )}

      <div className="admin-preview-tabs" role="tablist" aria-label="Разделы просмотра">
        <button type="button" className={tabClass("dashboard")} onClick={() => setTab("dashboard")}>
          Главная
        </button>
        <button type="button" className={tabClass("runs")} onClick={() => setTab("runs")}>
          Пробежки
        </button>
        <button type="button" className={tabClass("volunteering")} onClick={() => setTab("volunteering")}>
          Волонтёрство
        </button>
        <button type="button" className={tabClass("map")} onClick={() => setTab("map")}>
          Карта
        </button>
      </div>

      {loading && tab === "dashboard" && <p className="muted">Загрузка…</p>}
      {error && tab === "dashboard" && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!loading && !error && tab === "dashboard" && stats && (
        <AppDataSourceProvider source={previewDataSource}>
          <>
            <div className="stats-grid stats-grid-primary">
              <DashboardStatCard
                value={stats.total_runs ?? 0}
                label={runsCapLabel(stats.total_runs ?? 0)}
                variant="runs"
              />
              <DashboardStatCard
                value={stats.total_volunteering ?? 0}
                label={volunteeringCapLabel(stats.total_volunteering ?? 0)}
                variant="volunteering"
              />
            </div>
            <DashboardAnalytics
              analytics={stats.analytics}
              totalRuns={stats.total_runs ?? 0}
              totalVolunteering={stats.total_volunteering ?? 0}
            />
          </>
        </AppDataSourceProvider>
      )}

      {tab === "runs" && (
        <AppDataSourceProvider source={previewDataSource}>
          <RunsContent />
        </AppDataSourceProvider>
      )}

      {tab === "volunteering" && (
        <AppDataSourceProvider source={previewDataSource}>
          <VolunteeringContent />
        </AppDataSourceProvider>
      )}

      {tab === "map" && (
        <section className="card admin-preview-map">
          <UserMapPanel
            loadVisitedMap={loadPreviewVisitedMap}
            loadCatalogMap={getCatalogLocationsMap}
            loadCatalogTable={loadPreviewCatalogTable}
            visitedTabLabel="Визиты пользователя"
          />
        </section>
      )}
    </AppShell>
  );
}

export function AdminUserPreviewPage({ userId }: { userId: string }) {
  return <RequireAdmin>{() => <AdminUserPreviewContent userId={userId} />}</RequireAdmin>;
}
