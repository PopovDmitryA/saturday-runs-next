import { useCallback, useEffect, useRef, useState } from "react";
import { DashboardAnalytics } from "../../../components/DashboardAnalytics";
import { MyHistoryTeaser } from "../../../components/MyHistoryTeaser";
import { OnThisDayCard } from "../../../components/OnThisDayCard";
import { ProfileLinkSection } from "../../../components/ProfileLinkSection";
import { RecentRunsRating } from "../../../components/RecentRunsRating";
import { RequireAuth } from "../../../components/RequireAuth";
import { GoalsTeaser } from "../../dashboard/GoalsTeaser";
import { PublicProfileShareBlock } from "../../dashboard/DashboardPage";
import {
  getDashboard,
  getMyHistory,
  getOnThisDay,
  type DashboardResponse,
  type User,
} from "../../../lib/api";
import { formatDuration, pluralFormRu } from "../../../lib/format";
import {
  PORTAL_CABINET_HISTORY_HREF,
  PORTAL_CABINET_MAP_HREF,
  PORTAL_CABINET_RUNS_HREF,
  PORTAL_CABINET_VOLUNTEERING_HREF,
  PORTAL_LOGIN_HREF,
} from "../../../lib/portalRoutes";
import { PortalCabinetShell } from "./PortalCabinetShell";

// «00:23:12» → «23:12»: в герое часы почти всегда нулевые, укорачиваем.
export function formatHeroFinishTime(totalSec: number): string {
  return formatDuration(totalSec).replace(/^00:/, "");
}

const RUN_FORMS = ["пробежка", "пробежки", "пробежек"] as const;
const VOL_FORMS = ["волонтёрство", "волонтёрства", "волонтёрств"] as const;
const LOCATION_FORMS = ["локация", "локации", "локаций"] as const;
const SATURDAY_FORMS = ["суббота", "субботы", "суббот"] as const;

function heroGreeting(): string {
  const hour = new Date().getHours();
  if (hour >= 5 && hour < 12) {
    return "Доброе утро";
  }
  if (hour >= 12 && hour < 18) {
    return "Добрый день";
  }
  return "Добрый вечер";
}

function DashboardHero({ data }: { data: DashboardResponse }) {
  const stats = data.stats;
  const analytics = stats.analytics;
  const streak = analytics.saturday_streak_current ?? analytics.saturday_streak;

  return (
    <section className="portal-cab-hero">
      <h1 className="portal-cab-hero-title">{heroGreeting()}!</h1>
      <p className="portal-cab-hero-sub">
        {streak > 0
          ? `Текущая серия — ${streak} ${pluralFormRu(streak, SATURDAY_FORMS)} подряд. Так держать!`
          : "Ваша сводная статистика по всем беговым системам."}
      </p>
      <div className="portal-cab-hero-stats">
        <a className="portal-cab-hero-stat portal-cab-hero-stat-runs" href={PORTAL_CABINET_RUNS_HREF}>
          <div className="portal-cab-hero-stat-value">{stats.total_runs}</div>
          <div className="portal-cab-hero-stat-label">
            {pluralFormRu(stats.total_runs, RUN_FORMS)}
          </div>
        </a>
        <a
          className="portal-cab-hero-stat portal-cab-hero-stat-vol"
          href={PORTAL_CABINET_VOLUNTEERING_HREF}
        >
          <div className="portal-cab-hero-stat-value">{stats.total_volunteering}</div>
          <div className="portal-cab-hero-stat-label">
            {pluralFormRu(stats.total_volunteering, VOL_FORMS)}
          </div>
        </a>
        <div className="portal-cab-hero-stat portal-cab-hero-stat-pr">
          <div className="portal-cab-hero-stat-value">
            {analytics.best_finish_time_sec != null
              ? formatHeroFinishTime(analytics.best_finish_time_sec)
              : "—"}
          </div>
          <div className="portal-cab-hero-stat-label">лучшее время на 5 км</div>
        </div>
        <a className="portal-cab-hero-stat portal-cab-hero-stat-geo" href={PORTAL_CABINET_MAP_HREF}>
          <div className="portal-cab-hero-stat-value">{analytics.unique_locations}</div>
          <div className="portal-cab-hero-stat-label">
            {pluralFormRu(analytics.unique_locations, LOCATION_FORMS)}
          </div>
        </a>
      </div>
    </section>
  );
}

function PortalDashboardContent({ user }: { user: User }) {
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const hasLoadedRef = useRef(false);

  const load = useCallback(async (options?: { background?: boolean }) => {
    const background = options?.background ?? hasLoadedRef.current;
    if (!background) {
      setLoading(true);
    }
    setError(null);
    try {
      const response = await getDashboard();
      setData(response);
      hasLoadedRef.current = true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить данные");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const stats = data?.stats;
  const byPlatform = stats?.by_platform ?? {};

  return (
    <PortalCabinetShell active="dashboard" user={user}>
      {loading && !data && <p className="muted">Загрузка…</p>}

      {error && (
        <div className="card error">
          <p>{error}</p>
          <button type="button" className="btn secondary" onClick={() => void load()}>
            Повторить
          </button>
        </div>
      )}

      {data && !error && (
        <>
          <DashboardHero data={data} />

          <OnThisDayCard load={getOnThisDay} shareBase="/share" />

          <GoalsTeaser />

          <RecentRunsRating />

          <MyHistoryTeaser load={getMyHistory} href={PORTAL_CABINET_HISTORY_HREF} />

          <DashboardAnalytics
            analytics={stats?.analytics}
            totalRuns={stats?.total_runs ?? 0}
            totalVolunteering={stats?.total_volunteering ?? 0}
          />

          {(data.public_slug ?? data.serial_id) != null && (
            <PublicProfileShareBlock handle={data.public_slug ?? data.serial_id!} />
          )}

          {data.sync_enqueued && (
            <div className="banner info">
              {user.is_admin ? (
                <>
                  Запущено автообновление (не чаще раза в сутки). Прогресс можно посмотреть в
                  разделе <a href="/admin/queue">«Админка → Очередь»</a>.
                </>
              ) : (
                <>Запущено автообновление (не чаще раза в сутки). Данные обновятся в фоне.</>
              )}
            </div>
          )}
        </>
      )}

      <ProfileLinkSection
        byPlatform={byPlatform}
        onLinksChange={() => void load({ background: true })}
      />
    </PortalCabinetShell>
  );
}

export function PortalCabinetDashboardPage() {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {(user) => <PortalDashboardContent user={user} />}
    </RequireAuth>
  );
}
