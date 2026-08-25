import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DashboardAnalytics } from "../../components/DashboardAnalytics";
import { DashboardStatCard } from "../../components/DashboardStatCard";
import { PromoLoginCard } from "../../components/PromoLoginCard";
import { ImageLightbox } from "../../components/ImageLightbox";
import { PortalFooter } from "../portal/PortalFooter";
import { PortalHeader } from "../portal/PortalHeader";
import { CABINET_TAB_SEGMENTS, profileTabHref } from "../../lib/portalRoutes";
import { NAV_ICONS, SiteSidebar, type SidebarExtraGroup } from "../portal/SiteSidebar";
import { PortalSectionBottomNav } from "../portal/PortalSectionBottomNav";
import "../portal/portal.css";
import "../portal/portalSection.css";
import { AppDataSourceProvider, createPublicProfileDataSource } from "../../lib/appDataSource";
import { AchievementsShowcase } from "../achievements/AchievementsPage";
import { UserMapPanel } from "../maps/UserMapPanel";
import { RunsContent } from "../runs/RunsPage";
import { VolunteeringContent } from "../volunteering/VolunteeringPage";
import { HistoryContent } from "../history/HistoryPage";
import { CoRunnersContent } from "../co_runners/CoRunnersPage";
import { PlatformBadge } from "../../components/PlatformBadge";
import { platformProfileUrl } from "../../lib/platformProfileUrl";
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
  type AdminPlatformLinkBrief,
  type AdminUserPreviewDashboard,
  type User,
} from "../../lib/api";
import { platformCodeLabel, runsCapLabel, volunteeringCapLabel } from "../../lib/format";

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

// Порядок платформ в шапке — как везде на сайте (кабинет, админка).
const PLATFORM_ORDER: Record<string, number> = { five_verst: 0, s95: 1, parkrun: 2, runpark: 3 };

/** Привязанные системы участника: бейдж — ссылка в его профиль на самой системе. */
function ProfilePlatformLinks({ links }: { links: AdminPlatformLinkBrief[] }) {
  const sorted = [...links].sort(
    (a, b) => (PLATFORM_ORDER[a.platform_code] ?? 99) - (PLATFORM_ORDER[b.platform_code] ?? 99),
  );
  if (!sorted.length) return null;
  return (
    <div className="public-profile-platforms">
      {sorted.map((link) => (
        <PlatformBadge
          key={link.platform_code}
          code={link.platform_code}
          href={platformProfileUrl(link)}
          title={`Открыть профиль на ${platformCodeLabel(link.platform_code)}`}
        />
      ))}
    </div>
  );
}

function ProfileShell({
  children,
  profileName,
  profileAvatarUrl,
  profileAvatarFullUrl,
  platformLinks,
  tabsGroup,
}: {
  children: React.ReactNode;
  currentUser: User | null;
  profileName: string | null;
  profileAvatarUrl?: string | null;
  /** Оригинал аватарки участника — раскрывается по клику на неё. */
  profileAvatarFullUrl?: string | null;
  /** Привязанные системы — бейджи-ссылки под именем участника. */
  platformLinks?: AdminPlatformLinkBrief[];
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
                  <ProfileAvatar
                    url={profileAvatarUrl}
                    fullUrl={profileAvatarFullUrl}
                    name={profileName}
                  />
                )}
                <div className="public-profile-head-main">
                  <h1 className="public-profile-name">{profileName}</h1>
                  {platformLinks && <ProfilePlatformLinks links={platformLinks} />}
                </div>
              </div>
            )}
            {children}
          </div>
        </main>
      </div>
      <PortalFooter />
      {/* На телефоне сайдбар скрыт, и профиль оставался вообще без навигации
          сайта: уйти отсюда было не по чему. Панель та же, что у Локаций и
          Рейтингов, плюс страницы этого участника в шторке «Ещё». */}
      <PortalSectionBottomNav active={null} extraGroup={tabsGroup} />
    </div>
  );
}

/** Аватарка участника в шапке его профиля: клик раскрывает оригинал. */
function ProfileAvatar({
  url,
  fullUrl,
  name,
}: {
  url: string;
  fullUrl?: string | null;
  name?: string | null;
}) {
  const [zoomed, setZoomed] = useState(false);
  return (
    <>
      <button
        type="button"
        className="public-profile-avatar public-profile-avatar-button"
        onClick={() => setZoomed(true)}
        aria-label={name ? `Открыть аватар: ${name}` : "Открыть аватар"}
        title="Открыть аватар"
      >
        <img src={url} alt="" />
      </button>
      {zoomed && (
        <ImageLightbox src={fullUrl || url} alt={name ?? ""} onClose={() => setZoomed(false)} />
      )}
    </>
  );
}

function PublicProfileContent({
  serialId,
  handle,
  fallbackName,
  initialTab = "dashboard",
}: {
  serialId: number;
  // Хендл из адреса — по нему строим ссылки вкладок, чтобы адрес совпадал с тем,
  // что видит пользователь (ник, а не номер).
  handle: string;
  /**
   * Имя участника из резолва хендла. Полное имя приезжает вместе с дашбордом,
   * а он грузится только на вкладке «Главная»: без этого запаса заход по прямой
   * ссылке на карту или историю оставлял страницу вообще без имени — на
   * телефоне, где сайдбара нет, было не понять, чей это профиль.
   */
  fallbackName?: string | null;
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
  const profileName = dashboard ? profileDisplayName(dashboard.user) : (fallbackName ?? null);
  const tabClass = (value: ProfileTab) =>
    tab === value ? "admin-preview-tab active" : "admin-preview-tab";

  // Иконки те же, что у одноимённых разделов своего кабинета: в свёрнутом
  // рельсе сайдбара подписи скрыты, и без иконок пункты были не видны вовсе.
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
    // Имя участника — это «шапка» его раздела, поэтому ведёт на главную
    // профиля: раньше клик по нему не делал ничего.
    onTitleClick: () => setTab("dashboard"),
    avatarUrl: dashboard?.user.avatar_url ?? null,
    items: TAB_LABELS.map((item) => ({
      key: item.key,
      label: item.label,
      icon: NAV_ICONS[item.key],
      active: tab === item.key,
      onClick: () => setTab(item.key),
    })),
  };

  // Полоса вкладок на телефоне прокручивается горизонтально: активная вкладка
  // может оказаться за краем экрана (например, «Встречи» после захода по
  // прямой ссылке) — подтягиваем её в видимую часть.
  const tabsStripRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const active = tabsStripRef.current?.querySelector<HTMLElement>(`[data-tab="${tab}"]`);
    active?.scrollIntoView({ block: "nearest", inline: "center" });
    // currentUser в зависимостях не лишний: пока сессия проверяется, страница
    // рисует «Загрузка…» — полосы вкладок в разметке ещё нет, и прокручивать
    // нечего. Эффект должен повториться, когда она появится.
  }, [tab, currentUser]);

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
      profileAvatarFullUrl={dashboard?.user.avatar_full_url}
      platformLinks={dashboard?.platform_links}
      tabsGroup={tabsGroup}
    >

      {/* Мобильные чипы-вкладки: на десктопе навигация в сайдбаре. Список тот
          же, что и в сайдбаре (TAB_LABELS), — раньше он был продублирован
          руками и разъезжался при правках. */}
      <div
        className="admin-preview-tabs public-profile-tabs"
        role="tablist"
        aria-label="Разделы профиля"
        ref={tabsStripRef}
      >
        {TAB_LABELS.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={tab === item.key}
            data-tab={item.key}
            className={tabClass(item.key)}
            onClick={() => setTab(item.key)}
          >
            {item.label}
          </button>
        ))}
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
  const [resolvedName, setResolvedName] = useState<string | null>(null);
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
          if (cancelled) {
            return;
          }
          setResolvedName(res.display_name);
          const slug = res.public_slug?.trim();
          if (slug) {
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
        setResolvedName(res.display_name);
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
  return (
    <PublicProfileContent
      serialId={serialId}
      handle={handle}
      fallbackName={resolvedName}
      initialTab={initialTab}
    />
  );
}
