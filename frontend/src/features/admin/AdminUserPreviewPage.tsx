import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityDateLink } from "../../components/ActivityDateLink";
import { AppShell } from "../../components/AppShell";
import { DashboardAnalytics } from "../../components/DashboardAnalytics";
import { DashboardStatCard } from "../../components/DashboardStatCard";
import { PlatformBadge } from "../../components/PlatformBadge";
import { RequireAdmin } from "../../components/RequireAdmin";
import { AppDataSourceProvider, createAdminPreviewDataSource } from "../../lib/appDataSource";
import { AdminSubnav } from "./AdminSubnav";
import { UserMapPanel } from "../maps/UserMapPanel";
import {
  getAdminUserPreviewDashboard,
  getAdminUserPreviewRuns,
  getAdminUserPreviewVolunteering,
  getAdminUserPreviewVisitedMap,
  getAdminUserPreviewCatalogTable,
  getCatalogLocationsMap,
  type AdminUserPreviewDashboard,
  type RunItem,
  type VolunteeringItem,
} from "../../lib/api";
import { formatDateTime, formatFinishTimeValue, runsCapLabel, volunteeringCapLabel } from "../../lib/format";
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
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [volunteering, setVolunteering] = useState<VolunteeringItem[]>([]);
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

  const loadRuns = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await getAdminUserPreviewRuns(userId);
      setRuns(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить пробежки");
    } finally {
      setLoading(false);
    }
  }, [userId]);

  const loadVolunteering = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await getAdminUserPreviewVolunteering(userId);
      setVolunteering(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить волонтёрство");
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    if (tab === "dashboard") {
      void loadDashboard();
      return;
    }
    if (tab === "runs") {
      void loadRuns();
      return;
    }
    if (tab === "volunteering") {
      void loadVolunteering();
      return;
    }
  }, [tab, loadDashboard, loadRuns, loadVolunteering]);

  const loadPreviewVisitedMap = useCallback(
    () => getAdminUserPreviewVisitedMap(userId, false),
    [userId],
  );
  const loadPreviewCatalogTable = useCallback(
    () => getAdminUserPreviewCatalogTable(userId, false),
    [userId],
  );

  const stats = dashboard?.stats;
  const byPlatform = stats?.by_platform ?? {};

  const tabClass = (value: PreviewTab) =>
    tab === value ? "admin-preview-tab active" : "admin-preview-tab";

  const platformLinksBlock = useMemo(() => {
    if (!dashboard?.platform_links.length) {
      return <p className="muted">Профили не привязаны.</p>;
    }
    return (
      <ul className="admin-preview-links">
        {dashboard.platform_links.map((link) => (
          <li key={`${link.platform_code}-${link.external_user_id}`}>
            <PlatformBadge code={link.platform_code} />
            <a href={link.external_url} target="_blank" rel="noreferrer">
              {link.external_user_id}
            </a>
            {link.display_name && <span className="muted"> · {link.display_name}</span>}
          </li>
        ))}
      </ul>
    );
  }, [dashboard?.platform_links]);

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
          {platformLinksBlock}
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

      {loading && tab !== "map" && <p className="muted">Загрузка…</p>}
      {error && tab !== "map" && (
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
            {Object.keys(byPlatform).length > 0 && (
              <section className="card">
                <h3 className="section-title">По платформам</h3>
                <ul className="admin-preview-platform-stats">
                  {Object.entries(byPlatform).map(([code, row]) => (
                    <li key={code}>
                      <PlatformBadge code={code} />
                      <span>
                        {row.runs ?? 0} / {row.volunteering ?? 0} вол.
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        </AppDataSourceProvider>
      )}

      {!loading && !error && tab === "runs" && (
        <section className="card">
          {runs.length === 0 ? (
            <p className="muted">Пробежек нет.</p>
          ) : (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Дата</th>
                    <th>Платформа</th>
                    <th>Локация</th>
                    <th>Время</th>
                    <th>Место</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => (
                    <tr key={`${run.platform_code}-${run.event_date}-${run.location_name}-${run.finish_time_display ?? ""}`}>
                      <td>
                        <ActivityDateLink date={run.event_date} url={run.event_url} />
                      </td>
                      <td>
                        <PlatformBadge code={run.platform_code} />
                      </td>
                      <td>{run.location_name}</td>
                      <td>{formatFinishTimeValue(run.finish_time_display, run.finish_time_sec)}</td>
                      <td>{run.position ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {!loading && !error && tab === "volunteering" && (
        <section className="card">
          {volunteering.length === 0 ? (
            <p className="muted">Записей о волонтёрстве нет.</p>
          ) : (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Дата</th>
                    <th>Платформа</th>
                    <th>Локация</th>
                    <th>Роль</th>
                  </tr>
                </thead>
                <tbody>
                  {volunteering.map((item) => (
                    <tr key={`${item.platform_code}-${item.event_date}-${item.location_name}-${item.role}`}>
                      <td>
                        <ActivityDateLink date={item.event_date} url={item.event_url} />
                      </td>
                      <td>
                        <PlatformBadge code={item.platform_code} />
                      </td>
                      <td>{item.location_name}</td>
                      <td>{item.role ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
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
