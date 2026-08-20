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
import {
  PORTAL_CABINET_HISTORY_HREF,
  PORTAL_CABINET_SHARE_HREF,
  PORTAL_LOGIN_HREF,
} from "../../../lib/portalRoutes";
import { PortalCabinetShell } from "./PortalCabinetShell";
import { CabinetProfileHeader } from "./CabinetProfileHeader";
import { CabinetBentoGrid } from "./CabinetBentoGrid";
import { CabinetAllStats } from "./CabinetAllStats";

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
          <CabinetProfileHeader data={data} user={user} />

          <CabinetBentoGrid
            data={data}
            slots={{
              onThisDay: (
                <OnThisDayCard load={getOnThisDay} shareBase={PORTAL_CABINET_SHARE_HREF} />
              ),
              goals: <GoalsTeaser />,
              history: <MyHistoryTeaser load={getMyHistory} href={PORTAL_CABINET_HISTORY_HREF} />,
              rating: <RecentRunsRating />,
            }}
          />

          <CabinetAllStats>
            <DashboardAnalytics
              showHomeLocationWarning
              hidePanels
              analytics={stats?.analytics}
              totalRuns={stats?.total_runs ?? 0}
              totalVolunteering={stats?.total_volunteering ?? 0}
            />
          </CabinetAllStats>

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
