import { useCallback, useEffect, useState } from "react";
import { DashboardAnalytics } from "../../components/DashboardAnalytics";
import { DashboardStatCard } from "../../components/DashboardStatCard";
import { OnThisDayCard } from "../../components/OnThisDayCard";
import { AppDataSourceProvider, demoDataSource } from "../../lib/appDataSource";
import {
  demoGetOnThisDay,
  getDemoDashboard,
  type AdminUserPreviewDashboard,
} from "../../lib/api";
import { runsCapLabel, volunteeringCapLabel } from "../../lib/format";
import { DemoLinkedProfiles, DemoShell } from "./DemoShell";

function demoOwnerLabel(dashboard: AdminUserPreviewDashboard | null): string | undefined {
  if (!dashboard) {
    return undefined;
  }
  if (dashboard.user.display_name) {
    return dashboard.user.display_name;
  }
  if (dashboard.user.telegram_username) {
    return `@${dashboard.user.telegram_username.replace(/^@/, "")}`;
  }
  return undefined;
}

function DemoDashboardContent() {
  const [data, setData] = useState<AdminUserPreviewDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await getDemoDashboard();
      setData(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить демо-профиль");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const stats = data?.stats;

  return (
    <DemoShell title="Главная" ownerLabel={demoOwnerLabel(data)}>
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
          <OnThisDayCard load={demoGetOnThisDay} shareBase="/share" />

          <div className="stats-grid stats-grid-primary">
            <DashboardStatCard
              href="/demo/runs"
              value={stats?.total_runs ?? 0}
              label={runsCapLabel(stats?.total_runs ?? 0)}
              variant="runs"
            />
            <DashboardStatCard
              href="/demo/volunteering"
              value={stats?.total_volunteering ?? 0}
              label={volunteeringCapLabel(stats?.total_volunteering ?? 0)}
              variant="volunteering"
            />
          </div>

          <DashboardAnalytics
            analytics={stats?.analytics}
            totalRuns={stats?.total_runs ?? 0}
            totalVolunteering={stats?.total_volunteering ?? 0}
          />

          <DemoLinkedProfiles links={data.platform_links} />
        </>
      )}
    </DemoShell>
  );
}

export function DemoDashboardPage() {
  return (
    <AppDataSourceProvider source={demoDataSource}>
      <DemoDashboardContent />
    </AppDataSourceProvider>
  );
}
