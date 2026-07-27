import { useCallback, useEffect, useMemo, useState } from "react";
import { DashboardAnalytics } from "../../components/DashboardAnalytics";
import { DashboardStatCard } from "../../components/DashboardStatCard";
import { PromoLoginCard } from "../../components/PromoLoginCard";
import { PortalHeader } from "../portal/PortalHeader";
import { CABINET_TAB_SEGMENTS, profileTabHref } from "../../lib/portalRoutes";
import { SiteSidebar, type SidebarExtraGroup } from "../portal/SiteSidebar";
import "../portal/portal.css";
import "../portal/portalSection.css";
import { AppDataSourceProvider, createPublicProfileDataSource } from "../../lib/appDataSource";
import { AchievementsShowcase } from "../achievements/AchievementsPage";
import { UserMapPanel } from "../maps/UserMapPanel";
import { RunsContent } from "../runs/RunsPage";
import { VolunteeringContent } from "../volunteering/VolunteeringPage";
import { HistoryContent } from "../history/HistoryPage";
import { CoRunnersContent } from "../co_runners/CoRunnersPage";
import {
  ApiError,
  getCurrentUser,
  getPublicProfileDashboard,
  getPublicProfileHistory,
  getPublicProfileVisitedMap,
  getPublicProfileCatalogTable,
  getPublicProfileAchievements,
  getPublicProfileCoRunners,
  getPublicProfileCoRunnerMeetings,
  getCatalogLocationsMap,
  resolveProfileHandle,
  type AchievementsResponse,
  type AdminUserPreviewDashboard,
  type User,
} from "../../lib/api";
import { runsCapLabel, volunteeringCapLabel } from "../../lib/format";

type ProfileTab = "dashboard" | "runs" | "volunteering" | "map" | "achievements" | "history" | "meetings";

// Сегмент адреса → вкладка. Сегменты те же, что у кабинета (CABINET_TAB_SEGMENTS),
// чтобы свой и чужой профиль имели одинаковые адреса вкладок.
const PROFILE_SEGMENT_TO_TAB: Record<string, ProfileTab> = Object.fromEntries(
  Object.entries(CABINET_TAB_SEGMENTS)
    .filter(([, segment]) => segment)
    .map(([tab, segment]) => [segment, tab as ProfileTab]),
);

function profileDisplayName(user: AdminUserPreviewDashboard["user"]): string {
  if (user.display_name) return user.display_name;
  return `Участник ${user.id.slice(0, 8)}`;
}

function ProfileShell({
  children,
  profileName,
  profileAvatarUrl,
  tabsGroup,
}: {
  children: React.ReactNode;
  currentUser: User | null;
  profileName: string | null;
  profileAvatarUrl?: string | null;
  /** Вкладки профиля для единого сайдбара (группа с именем участника). */
  tabsGroup?: SidebarExtraGroup;
}) {
  // Публичный профиль в едином каркасе с сайдбаром: вкладки профиля живут в
  // сайдбаре (на мобиле — чипы над контентом, сайдбар там скрыт).
  return (
    <div className="portal-section-page">
      <PortalHeader />
      <div className="portal-cab-layout">
        <SiteSidebar active={null} extraGroup={tabsGroup} />
        <main className="portal-cab-main portal-section">
          <div className="portal-cab-stack">
            {profileName && (
              <div className="public-profile-head">
                {profileAvatarUrl && (
                  <img className="public-profile-avatar" src={profileAvatarUrl} alt="" />
                )}
                <h1 className="public-profile-name">{profileName}</h1>
              </div>
            )}
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}

function PublicProfileContent({
  serialId,
  handle,
  initialTab = "dashboard",
}: {
  serialId: number;
  // Хендл из адреса — по нему строим ссылки вкладок, чтобы адрес совпадал с тем,
  // что видит пользователь (ник, а не номер).
  handle: string;
  initialTab?: ProfileTab;
}) {
  const dataSource = useMemo(() => createPublicProfileDataSource(serialId), [serialId]);
  const [tab, setTabState] = useState<ProfileTab>(initialTab);

  // Вкладка живёт в адресе: иначе ссылкой на чужую карту не поделиться —
  // в строке браузера всегда оставалась бы главная страница участника.
  const setTab = useCallback(
    (next: ProfileTab) => {
      setTabState(next);
      const href = profileTabHref(handle, next);
      if (window.location.pathname !== href) {
        window.history.pushState({ tab: next }, "", href);
      }
    },
    [handle],
  );

  // Кнопка «назад» должна возвращать на предыдущую вкладку, а не уводить с профиля.
  useEffect(() => {
    const onPop = () => {
      const segment = window.location.pathname.split("/")[3] ?? "";
      const restored = PROFILE_SEGMENT_TO_TAB[segment];
      setTabState(restored ?? "dashboard");
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
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

  const [achievements, setAchievements] = useState<AchievementsResponse | null>(null);
  const [achievementsError, setAchievementsError] = useState<string | null>(null);
  const [achievementsPlatform, setAchievementsPlatform] = useState<string | null>(null);
  const [achievementsSwitching, setAchievementsSwitching] = useState(false);

  const loadAchievements = useCallback(
    async (platform: string | null) => {
      setAchievementsError(null);
      setAchievementsSwitching(true);
      try {
        setAchievements(await getPublicProfileAchievements(serialId, platform ?? undefined));
      } catch (err) {
        setAchievementsError(err instanceof Error ? err.message : "Не удалось загрузить достижения");
      } finally {
        setAchievementsSwitching(false);
      }
    },
    [serialId],
  );

  useEffect(() => {
    if (tab === "achievements" && !achievements && !achievementsError) {
      void loadAchievements(achievementsPlatform);
    }
  }, [tab, achievements, achievementsError, achievementsPlatform, loadAchievements]);

  const handleAchievementsPlatformChange = useCallback(
    (platform: string | null) => {
      setAchievementsPlatform(platform);
      void loadAchievements(platform);
    },
    [loadAchievements],
  );

  const loadVisitedMap = useCallback(() => getPublicProfileVisitedMap(serialId, false), [serialId]);
  const loadCatalogTable = useCallback(() => getPublicProfileCatalogTable(serialId, false), [serialId]);
  const loadHistory = useCallback(() => getPublicProfileHistory(serialId, false), [serialId]);
  const loadCoRunners = useCallback(() => getPublicProfileCoRunners(serialId), [serialId]);
  const loadCoRunnerMeetings = useCallback(
    (participantKey: string) => getPublicProfileCoRunnerMeetings(serialId, participantKey),
    [serialId],
  );

  const stats = dashboard?.stats;
  const profileName = dashboard ? profileDisplayName(dashboard.user) : null;
  const tabClass = (value: ProfileTab) =>
    tab === value ? "admin-preview-tab active" : "admin-preview-tab";

  const TAB_LABELS: { key: ProfileTab; label: string }[] = [
    { key: "dashboard", label: "Главная" },
    { key: "runs", label: "Пробежки" },
    { key: "volunteering", label: "Волонтёрство" },
    { key: "map", label: "Карта" },
    { key: "achievements", label: "Достижения" },
    { key: "history", label: "История" },
    { key: "meetings", label: "Встречи" },
  ];
  // Группу вкладок отдаём всегда, а не только когда загружено имя: имя приезжает
  // вместе с дашбордом, а он грузится лишь на вкладке «Главная». Из-за этого
  // заход по прямой ссылке на карту или достижения оставлял сайдбар без вкладок
  // — уйти с открытой вкладки было некуда, кроме как через адресную строку.
  const tabsGroup: SidebarExtraGroup = {
    title: profileName ?? "Профиль участника",
    items: TAB_LABELS.map((item) => ({
      key: item.key,
      label: item.label,
      active: tab === item.key,
      onClick: () => setTab(item.key),
    })),
  };

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
    <ProfileShell
      currentUser={currentUser}
      profileName={profileName}
      profileAvatarUrl={dashboard?.user.avatar_url}
      tabsGroup={tabsGroup}
    >

      {/* Мобильные чипы-вкладки: на десктопе навигация в сайдбаре. */}
      <div className="admin-preview-tabs public-profile-tabs" role="tablist" aria-label="Разделы профиля">
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
        <button type="button" className={tabClass("achievements")} onClick={() => setTab("achievements")}>
          Достижения
        </button>
        <button type="button" className={tabClass("history")} onClick={() => setTab("history")}>
          История
        </button>
        <button type="button" className={tabClass("meetings")} onClick={() => setTab("meetings")}>
          Встречи
        </button>
      </div>

      {currentUser === null && (
        <PromoLoginCard
          icon="⚡"
          title="Хотите такую же статистику?"
          text="Зарегистрируйтесь и привяжите профили своих беговых систем — вся история пробежек, рекорды, достижения и карта локаций соберутся в вашем личном кабинете."
          cta="Создать свой профиль"
        />
      )}

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

      {tab === "achievements" && (
        <>
          {achievementsError && (
            <div className="card error">
              <p>{achievementsError}</p>
              <button type="button" className="btn secondary" onClick={() => void loadAchievements(achievementsPlatform)}>
                Повторить
              </button>
            </div>
          )}
          {!achievementsError && !achievements && <p className="muted">Загрузка…</p>}
          {achievements && (
            <AchievementsShowcase
              data={achievements}
              platformFilter={achievementsPlatform}
              onPlatformFilterChange={handleAchievementsPlatformChange}
              platformSwitching={achievementsSwitching}
            />
          )}
        </>
      )}

      {tab === "history" && (
        <HistoryContent
          load={loadHistory}
          title="История участника"
          description="Ключевые вехи беговой истории: первая пробежка, клубы, личные рекорды, новые регионы и волонтёрство."
          emptyText="У этого участника пока нет вех."
        />
      )}

      {tab === "meetings" && (
        <CoRunnersContent load={loadCoRunners} loadMeetings={loadCoRunnerMeetings} />
      )}
    </ProfileShell>
  );
}

// Хэндл из URL (/users/{handle}) — либо числовой serial_id, либо vanity-slug.
// Числовой используем напрямую; slug сначала резолвим в serial_id.
export function PublicProfilePage({
  handle,
  initialTab,
}: {
  handle: string;
  initialTab?: ProfileTab;
}) {
  const isNumeric = /^\d+$/.test(handle);
  const [serialId, setSerialId] = useState<number | null>(isNumeric ? Number(handle) : null);
  const [state, setState] = useState<"ready" | "resolving" | "not-found">(
    isNumeric ? "ready" : "resolving",
  );

  useEffect(() => {
    let cancelled = false;
    if (isNumeric) {
      setSerialId(Number(handle));
      setState("ready");
      // Пришли по номеру (например, из таблицы рейтинга) — если у участника
      // есть ник, показываем в адресной строке его: ссылкой удобнее делиться.
      resolveProfileHandle(handle)
        .then((res) => {
          const slug = res.public_slug?.trim();
          if (!cancelled && slug) {
            // Сегмент вкладки сохраняем: иначе переход по ссылке на чужую карту
            // по числовому адресу сбрасывал бы её на главную страницу профиля.
            const segment = window.location.pathname.split("/")[3] ?? "";
            const suffix = segment ? `/${encodeURIComponent(segment)}` : "";
            window.history.replaceState(null, "", `/users/${encodeURIComponent(slug)}${suffix}`);
          }
        })
        .catch(() => {
          // адрес остаётся числовым — не критично
        });
      return () => {
        cancelled = true;
      };
    }
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
  return <PublicProfileContent serialId={serialId} handle={handle} initialTab={initialTab} />;
}
