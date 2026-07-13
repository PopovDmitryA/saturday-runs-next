import { useCallback, useEffect, useRef, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { DashboardAnalytics } from "../../components/DashboardAnalytics";
import { DashboardStatCard } from "../../components/DashboardStatCard";
import { OnThisDayCard } from "../../components/OnThisDayCard";
import { ProfileLinkSection } from "../../components/ProfileLinkSection";
import { RecentRunsRating } from "../../components/RecentRunsRating";
import { RequireAuth } from "../../components/RequireAuth";
import { getDashboard, getOnThisDay, type DashboardResponse } from "../../lib/api";
import { GoalsTeaser } from "./GoalsTeaser";
import { runsCapLabel, volunteeringCapLabel } from "../../lib/format";

function PublicProfileShareBlock({ handle }: { handle: string | number }) {
  const [copied, setCopied] = useState(false);
  // Если задан уникальный slug — ссылка на него, иначе на числовой serial_id.
  const url = `${window.location.origin}/users/${handle}`;

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore — clipboard API unavailable, user can still select the link text
    }
  }, [url]);

  return (
    <div className="card public-profile-share">
      <p className="public-profile-share-title">Мой публичный профиль</p>
      <p className="muted">Поделитесь ссылкой — по ней видна ваша статистика пробежек и волонтёрств.</p>
      <div className="public-profile-share-row">
        <input className="input" type="text" value={url} readOnly onFocus={(e) => e.target.select()} />
        <button type="button" className="btn secondary" onClick={() => void handleCopy()}>
          {copied ? "Скопировано ✓" : "Скопировать"}
        </button>
        <a className="btn btn-ghost" href={url} target="_blank" rel="noreferrer">
          Открыть →
        </a>
      </div>
    </div>
  );
}

function DashboardContent({ isAdmin }: { isAdmin: boolean }) {
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

  useEffect(() => {
    if (window.location.pathname === "/profiles") {
      window.history.replaceState(null, "", "/dashboard#profiles");
    }
  }, []);

  const stats = data?.stats;
  const byPlatform = stats?.by_platform ?? {};

  return (
    <AppShell title="Главная">
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
          <OnThisDayCard load={getOnThisDay} shareBase="/share" />

          <div className="stats-grid stats-grid-primary">
            <DashboardStatCard
              href="/runs"
              value={stats?.total_runs ?? 0}
              label={runsCapLabel(stats?.total_runs ?? 0)}
              variant="runs"
            />
            <DashboardStatCard
              href="/volunteering"
              value={stats?.total_volunteering ?? 0}
              label={volunteeringCapLabel(stats?.total_volunteering ?? 0)}
              variant="volunteering"
            />
          </div>

          <GoalsTeaser />

          <RecentRunsRating />

          <DashboardAnalytics
            analytics={stats?.analytics}
            totalRuns={stats?.total_runs ?? 0}
            totalVolunteering={stats?.total_volunteering ?? 0}
          />

          {(data.public_slug ?? data.serial_id) != null && (
            <PublicProfileShareBlock handle={data.public_slug ?? data.serial_id!} />
          )}

          {isAdmin && data.sync_enqueued && (
            <div className="banner info">
              Запущено автообновление (не чаще раза в сутки). Прогресс можно посмотреть в разделе{" "}
              <a href="/admin/queue">«Админка → Очередь»</a>.
            </div>
          )}
          {!isAdmin && data.sync_enqueued && (
            <div className="banner info">
              Запущено автообновление (не чаще раза в сутки). Данные обновятся в фоне.
            </div>
          )}
        </>
      )}

      <ProfileLinkSection
        byPlatform={byPlatform}
        onLinksChange={() => void load({ background: true })}
      />
    </AppShell>
  );
}

export function DashboardPage() {
  return <RequireAuth>{(user) => <DashboardContent isAdmin={user.is_admin} />}</RequireAuth>;
}
