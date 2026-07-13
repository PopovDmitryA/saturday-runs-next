import { useCallback, useEffect, useMemo, useState } from "react";
import { DashboardAnalytics } from "../../components/DashboardAnalytics";
import { DashboardStatCard } from "../../components/DashboardStatCard";
import { SiteHeader } from "../../components/SiteHeader";
import { AppDataSourceProvider, createPublicProfileDataSource } from "../../lib/appDataSource";
import { UserMapPanel } from "../maps/UserMapPanel";
import { RunsContent } from "../runs/RunsPage";
import { VolunteeringContent } from "../volunteering/VolunteeringPage";
import { HistoryContent } from "../history/HistoryPage";
import {
  ApiError,
  getCurrentUser,
  logout,
  getPublicProfileDashboard,
  getPublicProfileHistory,
  getPublicProfileVisitedMap,
  getPublicProfileCatalogTable,
  getCatalogLocationsMap,
  resolveProfileHandle,
  type AdminUserPreviewDashboard,
  type User,
} from "../../lib/api";
import { runsCapLabel, volunteeringCapLabel } from "../../lib/format";
import { APP_NAV_ITEMS, PUBLIC_NAV_ITEMS } from "../../lib/siteNav";
import { SITE_HOME_HREF, SITE_PUBLIC_HOME_HREF } from "../../lib/siteBrand";

type ProfileTab = "dashboard" | "runs" | "volunteering" | "map" | "history";

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

  useEffect(() => {
    if (tab === "dashboard") void loadDashboard();
  }, [tab, loadDashboard]);

  const loadVisitedMap = useCallback(() => getPublicProfileVisitedMap(serialId, false), [serialId]);
  const loadCatalogTable = useCallback(() => getPublicProfileCatalogTable(serialId, false), [serialId]);
  const loadHistory = useCallback(() => getPublicProfileHistory(serialId, false), [serialId]);

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
        <button type="button" className={tabClass("history")} onClick={() => setTab("history")}>
          История
        </button>
      </div>

      {loading && tab === "dashboard" && <p className="muted">Загрузка…</p>}
      {error && tab === "dashboard" && <div className="card error"><p>{error}</p></div>}

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

      {tab === "runs" && (
        <AppDataSourceProvider source={dataSource}>
          <RunsContent />
        </AppDataSourceProvider>
      )}

      {tab === "volunteering" && (
        <AppDataSourceProvider source={dataSource}>
          <VolunteeringContent />
        </AppDataSourceProvider>
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

      {tab === "history" && (
        <HistoryContent
          load={loadHistory}
          title="История участника"
          description="Ключевые вехи беговой истории: первая пробежка, клубы, личные рекорды, новые регионы и волонтёрство."
          emptyText="У этого участника пока нет вех."
        />
      )}
    </ProfileShell>
  );
}

// Хэндл из URL (/users/{handle}) — либо числовой serial_id, либо vanity-slug.
// Числовой используем напрямую; slug сначала резолвим в serial_id.
export function PublicProfilePage({ handle }: { handle: string }) {
  const isNumeric = /^\d+$/.test(handle);
  const [serialId, setSerialId] = useState<number | null>(isNumeric ? Number(handle) : null);
  const [state, setState] = useState<"ready" | "resolving" | "not-found">(
    isNumeric ? "ready" : "resolving",
  );

  useEffect(() => {
    if (isNumeric) {
      setSerialId(Number(handle));
      setState("ready");
      return;
    }
    let cancelled = false;
    setState("resolving");
    resolveProfileHandle(handle)
      .then((res) => {
        if (cancelled) return;
        setSerialId(res.serial_id);
        setState("ready");
      })
      .catch(() => {
        if (!cancelled) setState("not-found");
      });
    return () => {
      cancelled = true;
    };
  }, [handle, isNumeric]);

  if (state === "resolving") {
    return (
      <main className="app">
        <p className="muted">Загрузка…</p>
      </main>
    );
  }
  if (state === "not-found" || serialId == null) {
    return (
      <div className="shell">
        <div className="shell-content">
          <div className="card">
            <p className="muted">Участник не найден.</p>
          </div>
        </div>
      </div>
    );
  }
  return <PublicProfileContent serialId={serialId} />;
}
