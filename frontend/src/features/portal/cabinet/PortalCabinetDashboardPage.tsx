import { useCallback, useEffect, useRef, useState } from "react";
import { DashboardAnalytics } from "../../../components/DashboardAnalytics";
import { MyHistoryTeaser } from "../../../components/MyHistoryTeaser";
import { OnThisDayCard } from "../../../components/OnThisDayCard";
import { PlatformBadge } from "../../../components/PlatformBadge";
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
  PORTAL_CABINET_SHARE_HREF,
  PORTAL_CABINET_RUNS_HREF,
  PORTAL_CABINET_VOLUNTEERING_HREF,
  PORTAL_LOGIN_HREF,
} from "../../../lib/portalRoutes";
import { PortalCabinetShell, userLabel } from "./PortalCabinetShell";

// «00:23:12» → «23:12»: в герое часы почти всегда нулевые, укорачиваем.
export function formatHeroFinishTime(totalSec: number): string {
  return formatDuration(totalSec).replace(/^00:/, "");
}

// Порядок бейджей систем в герое — тот же, что в секции «Профили беговых
// систем» внизу страницы, чтобы клик по бейджу приводил к предсказуемой карточке.
const PLATFORM_ORDER = ["five_verst", "s95", "parkrun", "runpark"] as const;

/** Прокрутка к секции привязок внизу «Обзора» — цель бейджей систем в герое. */
function scrollToProfiles(): void {
  document.getElementById("profiles")?.scrollIntoView({ behavior: "smooth", block: "start" });
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

function heroInitials(name: string): string {
  const clean = name.replace(/^@/, "").trim();
  const parts = clean.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return clean.slice(0, 2).toUpperCase();
}

// Варианты героя дашборда: panel — панель без аватара (окончательный выбор
// Дмитрия, 19.07.2026, по умолчанию), card — та же панель с аватаром и именем,
// compact — одна строка почти без высоты. card/compact оставлены в коде на
// случай пересмотра, но нигде не выбираются активно. Тёмный вариант отклонён —
// кабинет остаётся в тонах портала.
export type HeroVariant = "card" | "panel" | "compact";

type HeroStat = {
  key: string;
  className: string;
  href?: string;
  value: string | number;
  label: string;
};

function buildHeroStats(data: Pick<DashboardResponse, "stats">): HeroStat[] {
  const stats = data.stats;
  const analytics = stats.analytics;
  return [
    {
      key: "runs",
      className: "portal-cab-hero-stat-runs",
      href: PORTAL_CABINET_RUNS_HREF,
      value: stats.total_runs,
      label: pluralFormRu(stats.total_runs, RUN_FORMS),
    },
    {
      key: "vol",
      className: "portal-cab-hero-stat-vol",
      href: PORTAL_CABINET_VOLUNTEERING_HREF,
      value: stats.total_volunteering,
      label: pluralFormRu(stats.total_volunteering, VOL_FORMS),
    },
    {
      key: "pr",
      className: "portal-cab-hero-stat-pr",
      value:
        analytics.best_finish_time_sec != null
          ? formatHeroFinishTime(analytics.best_finish_time_sec)
          : "—",
      label: "лучшее время на 5 км",
    },
    {
      key: "geo",
      className: "portal-cab-hero-stat-geo",
      href: PORTAL_CABINET_MAP_HREF,
      value: analytics.unique_locations,
      label: pluralFormRu(analytics.unique_locations, LOCATION_FORMS),
    },
  ];
}

/**
 * Бейджи привязанных систем — как на странице локации. Клик уводит к секции
 * «Профили беговых систем» внизу страницы: там привязка, отвязка и кнопка
 * обновления данных.
 */
function HeroPlatforms({ byPlatform }: { byPlatform: Record<string, unknown> }) {
  const codes = PLATFORM_ORDER.filter((code) => code in byPlatform);
  if (codes.length === 0) {
    return null;
  }
  return (
    <div className="portal-cab-hero-platforms">
      {codes.map((code) => (
        <PlatformBadge
          key={code}
          code={code}
          onClick={scrollToProfiles}
          title="Перейти к профилям беговых систем"
        />
      ))}
    </div>
  );
}

function HeroStatsGrid({ items }: { items: HeroStat[] }) {
  return (
    <div className="portal-cab-hero-stats">
      {items.map((item) =>
        item.href ? (
          <a key={item.key} className={`portal-cab-hero-stat ${item.className}`} href={item.href}>
            <div className="portal-cab-hero-stat-value">{item.value}</div>
            <div className="portal-cab-hero-stat-label">{item.label}</div>
          </a>
        ) : (
          <div key={item.key} className={`portal-cab-hero-stat ${item.className}`}>
            <div className="portal-cab-hero-stat-value">{item.value}</div>
            <div className="portal-cab-hero-stat-label">{item.label}</div>
          </div>
        ),
      )}
    </div>
  );
}

export function DashboardHero({
  data,
  userName,
  // Решение Дмитрия 19.07.2026: панель без аватара — окончательный выбор.
  variant = "panel",
  hrefForStat,
}: {
  data: Pick<DashboardResponse, "stats">;
  userName: string;
  variant?: HeroVariant;
  // Превью на демо-данных подменяет адреса: разделы под RequireAuth без
  // сессии выбросили бы на вход.
  hrefForStat?: (key: string, defaultHref: string) => string;
}) {
  const analytics = data.stats.analytics;
  const streak = analytics.saturday_streak_current ?? analytics.saturday_streak;
  const streakLine =
    streak > 0
      ? `Текущая серия — ${streak} ${pluralFormRu(streak, SATURDAY_FORMS)} подряд. Так держать!`
      : "Ваша сводная статистика по всем беговым системам.";
  const items = buildHeroStats(data).map((item) =>
    item.href && hrefForStat ? { ...item, href: hrefForStat(item.key, item.href) } : item,
  );

  if (variant === "card") {
    return (
      <section className="portal-cab-hero portal-cab-hero-card">
        <div className="portal-cab-hero-card-head">
          <span className="portal-cab-hero-card-avatar" aria-hidden="true">
            {heroInitials(userName)}
          </span>
          <div className="portal-cab-hero-card-id">
            <h1 className="portal-cab-hero-title">{userName}</h1>
            <p className="portal-cab-hero-sub">
              {heroGreeting()}! {streakLine}
            </p>
          </div>
        </div>
        <HeroPlatforms byPlatform={data.stats.by_platform} />
        <HeroStatsGrid items={items} />
      </section>
    );
  }

  if (variant === "compact") {
    return (
      <section className="portal-cab-hero portal-cab-hero-compact">
        <h1 className="portal-cab-hero-compact-name">{userName}</h1>
        <div className="portal-cab-hero-compact-stats">
          {items.map((item) =>
            item.href ? (
              <a key={item.key} className={`portal-cab-hero-stat ${item.className}`} href={item.href}>
                <span className="portal-cab-hero-stat-value">{item.value}</span>
                <span className="portal-cab-hero-stat-label">{item.label}</span>
              </a>
            ) : (
              <div key={item.key} className={`portal-cab-hero-stat ${item.className}`}>
                <span className="portal-cab-hero-stat-value">{item.value}</span>
                <span className="portal-cab-hero-stat-label">{item.label}</span>
              </div>
            ),
          )}
        </div>
      </section>
    );
  }

  return (
    <section className="portal-cab-hero">
      <h1 className="portal-cab-hero-title">{heroGreeting()}!</h1>
      <p className="portal-cab-hero-sub">{streakLine}</p>
      <HeroPlatforms byPlatform={data.stats.by_platform} />
      <HeroStatsGrid items={items} />
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
      <div className="portal-cab-stack">
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
          <DashboardHero data={data} userName={userLabel(user)} />

          <OnThisDayCard load={getOnThisDay} shareBase={PORTAL_CABINET_SHARE_HREF} />

          <GoalsTeaser />

          <RecentRunsRating />

          <MyHistoryTeaser
            load={getMyHistory}
            href={PORTAL_CABINET_HISTORY_HREF}
            shareBase={PORTAL_CABINET_SHARE_HREF}
          />

          <DashboardAnalytics
            showHomeLocationWarning
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
      </div>
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
