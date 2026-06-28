import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityDateCell } from "../../components/ActivityDateCell";
import { ActivityDateLink } from "../../components/ActivityDateLink";
import { DashboardAnalytics } from "../../components/DashboardAnalytics";
import { DashboardStatCard } from "../../components/DashboardStatCard";
import { GlobalPrFinishTime } from "../../components/GlobalPrFinishTime";
import { PlatformBadge } from "../../components/PlatformBadge";
import { SiteHeader } from "../../components/SiteHeader";
import { ActivityTableCols } from "../../components/activityTable/ActivityTableCols";
import { AppDataSourceProvider, createPublicProfileDataSource } from "../../lib/appDataSource";
import { UserMapPanel } from "../maps/UserMapPanel";
import {
  ApiError,
  getCurrentUser,
  logout,
  getPublicProfileDashboard,
  getAllPublicProfileRuns,
  getAllPublicProfileVolunteering,
  getPublicProfileVisitedMap,
  getPublicProfileCatalogTable,
  getCatalogLocationsMap,
  type AdminUserPreviewDashboard,
  type RunItem,
  type VolunteeringItem,
  type User,
} from "../../lib/api";
import { formatFinishTimeValue, runsCapLabel, volunteeringCapLabel } from "../../lib/format";
import { APP_NAV_ITEMS, PUBLIC_NAV_ITEMS } from "../../lib/siteNav";
import { SITE_HOME_HREF, SITE_PUBLIC_HOME_HREF } from "../../lib/siteBrand";

type ProfileTab = "dashboard" | "runs" | "volunteering" | "map";

function profileDisplayName(user: AdminUserPreviewDashboard["user"]): string {
  if (user.display_name) return user.display_name;
  return `Участник ${user.id.slice(0, 8)}`;
}

function ProfileShell({
  children,
  currentUser,
  profileName,
}: {
  children: React.ReactNode;
  currentUser: User | null;
  profileName: string | null;
}) {
  const handleLogout = async () => {
    await logout();
    window.location.href = "/";
  };

  return (
    <div className="shell">
      <SiteHeader
        homeHref={currentUser ? SITE_HOME_HREF : SITE_PUBLIC_HOME_HREF}
        navItems={currentUser ? APP_NAV_ITEMS : PUBLIC_NAV_ITEMS}
        activePath=""
        showAdminNav={currentUser?.is_admin ?? false}
        actions={
          currentUser ? (
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => void handleLogout()}>
              Выйти
            </button>
          ) : (
            <div className="demo-shell-actions">
              <a className="btn btn-ghost btn-sm" href={SITE_PUBLIC_HOME_HREF}>На главную</a>
              <a className="btn primary btn-sm" href="/login">Войти</a>
            </div>
          )
        }
      />
      <div className="shell-content">
        {profileName && (
          <div className="shell-user">
            <div className="shell-user-row">
              <h1 className="shell-user-name">{profileName}</h1>
            </div>
          </div>
        )}
        {children}
      </div>
    </div>
  );
}

function PublicProfileContent({ serialId }: { serialId: number }) {
  const dataSource = useMemo(() => createPublicProfileDataSource(serialId), [serialId]);
  const [tab, setTab] = useState<ProfileTab>("dashboard");
  const [dashboard, setDashboard] = useState<AdminUserPreviewDashboard | null>(null);
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [volunteering, setVolunteering] = useState<VolunteeringItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [currentUser, setCurrentUser] = useState<User | null | undefined>(undefined);

  useEffect(() => {
    getCurrentUser()
      .then((u) => setCurrentUser(u))
      .catch(() => setCurrentUser(null));
  }, []);

  const loadDashboard = useCallback(async () => {
    setLoading(true);
    setError(null);
    setForbidden(false);
    setNotFound(false);
    try {
      setDashboard(await getPublicProfileDashboard(serialId));
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) setForbidden(true);
      else if (err instanceof ApiError && err.status === 404) setNotFound(true);
      else setError(err instanceof Error ? err.message : "Не удалось загрузить профиль");
    } finally {
      setLoading(false);
    }
  }, [serialId]);

  const loadRuns = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRuns(await getAllPublicProfileRuns(serialId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить пробежки");
    } finally {
      setLoading(false);
    }
  }, [serialId]);

  const loadVolunteering = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setVolunteering(await getAllPublicProfileVolunteering(serialId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить волонтёрство");
    } finally {
      setLoading(false);
    }
  }, [serialId]);

  useEffect(() => {
    if (tab === "dashboard") void loadDashboard();
    else if (tab === "runs") void loadRuns();
    else if (tab === "volunteering") void loadVolunteering();
  }, [tab, loadDashboard, loadRuns, loadVolunteering]);

  const loadVisitedMap = useCallback(() => getPublicProfileVisitedMap(serialId, false), [serialId]);
  const loadCatalogTable = useCallback(() => getPublicProfileCatalogTable(serialId, false), [serialId]);

  const stats = dashboard?.stats;
  const profileName = dashboard ? profileDisplayName(dashboard.user) : null;
  const tabClass = (value: ProfileTab) =>
    tab === value ? "admin-preview-tab active" : "admin-preview-tab";

  if (currentUser === undefined) {
    return <main className="app"><p className="muted">Загрузка…</p></main>;
  }

  if (forbidden) {
    return (
      <ProfileShell currentUser={currentUser} profileName={null}>
        <div className="card public-profile-locked">
          <p className="public-profile-locked-icon">🔒</p>
          <h2>Профиль скрыт</h2>
          <p className="muted">Этот участник закрыл свой профиль от просмотра.</p>
        </div>
      </ProfileShell>
    );
  }

  if (notFound) {
    return (
      <ProfileShell currentUser={currentUser} profileName={null}>
        <div className="card">
          <p className="muted">Участник не найден.</p>
        </div>
      </ProfileShell>
    );
  }

  return (
    <ProfileShell currentUser={currentUser} profileName={profileName}>

      <div className="admin-preview-tabs" role="tablist" aria-label="Разделы профиля">
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
      {error && tab !== "map" && <div className="card error"><p>{error}</p></div>}

      {!loading && !error && tab === "dashboard" && stats && (
        <AppDataSourceProvider source={dataSource}>
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

      {!loading && !error && tab === "runs" && (
        <section className="card">
          {runs.length === 0 ? (
            <p className="muted">Пробежек нет.</p>
          ) : (
            <div className="table-scroll">
              <table className="data-table data-table-layout-fixed">
                <ActivityTableCols variant="runs" />
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
                      <td className="td-date">
                        <ActivityDateCell
                          date={<ActivityDateLink date={run.event_date} url={run.event_url} />}
                          badges={
                            <>
                              {run.is_test_event && <span className="badge">тест</span>}
                              {run.is_pr && <span className="badge badge-pr">PR</span>}
                            </>
                          }
                        />
                      </td>
                      <td className="td-platform"><PlatformBadge code={run.platform_code} /></td>
                      <td className="td-location">{run.location_name}</td>
                      <td className="td-time">
                        <GlobalPrFinishTime isGlobalPr={run.is_global_pr}>
                          {formatFinishTimeValue(run.finish_time_display, run.finish_time_sec)}
                        </GlobalPrFinishTime>
                      </td>
                      <td className="td-compact">{run.position ?? "—"}</td>
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
                      <td><ActivityDateLink date={item.event_date} url={item.event_url} /></td>
                      <td><PlatformBadge code={item.platform_code} /></td>
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
            loadVisitedMap={loadVisitedMap}
            loadCatalogMap={getCatalogLocationsMap}
            loadCatalogTable={loadCatalogTable}
            visitedTabLabel="Визиты"
          />
        </section>
      )}
    </ProfileShell>
  );
}

export function PublicProfilePage({ serialId }: { serialId: number }) {
  return <PublicProfileContent serialId={serialId} />;
}
