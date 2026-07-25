import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { AdminShell } from "./AdminShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import { PlatformBadge } from "../../components/PlatformBadge";
import { ChartColumnTooltip } from "../../components/ChartColumnTooltip";
import { getAdminSiteStats, type AdminSiteStatsResponse } from "../../lib/api";
import { formatChartDay, formatDate, formatDateTime, platformCodeLabel } from "../../lib/format";
import { AdminSubnav } from "./AdminSubnav";

const PERIODS = [
  { label: "1 день", days: 1 },
  { label: "3 дня", days: 3 },
  { label: "7 дней", days: 7 },
  { label: "30 дней", days: 30 },
  { label: "90 дней", days: 90 },
] as const;

const CHART_BAR_AREA_PX = 112;

type DayPoint = { date: string; value: number };

function maxValue(items: DayPoint[]): number {
  return items.reduce((max, item) => Math.max(max, item.value), 0);
}

function sumValues(items: DayPoint[]): number {
  return items.reduce((sum, item) => sum + item.value, 0);
}

function chartGridStyle(count: number): CSSProperties {
  return {
    gridTemplateColumns: `repeat(${Math.max(count, 1)}, minmax(0, 1fr))`,
  };
}

function DailyBarChart({
  title,
  data,
  ariaLabel,
  barClassName = "analytics-chart-bar-run",
  valueLabel,
  chartKey,
}: {
  title: string;
  data: DayPoint[];
  ariaLabel: string;
  barClassName?: string;
  valueLabel?: (value: number) => string;
  chartKey: string;
}) {
  const peak = maxValue(data);
  const periodTotal = sumValues(data);
  const formatValue = valueLabel ?? ((value: number) => String(value));
  const showBarValues = data.length <= 14;

  if (peak === 0) {
    return (
      <section className="card admin-stats-chart-card">
        <h3 className="admin-stats-chart-title">{title}</h3>
        <p className="muted">За выбранный период данных пока нет.</p>
      </section>
    );
  }

  return (
    <section className="card admin-stats-chart-card" key={chartKey}>
      <h3 className="admin-stats-chart-title">{title}</h3>
      <p className="admin-stats-chart-summary muted">За период: {formatValue(periodTotal)}</p>
      <div className="analytics-chart">
        <div
          className="analytics-chart-bars admin-stats-daily-bars"
          style={chartGridStyle(data.length)}
          role="img"
          aria-label={ariaLabel}
        >
          {data.map((item) => {
            const barHeight = Math.max(4, Math.round((item.value / peak) * CHART_BAR_AREA_PX));
            const dateLabel = formatDate(item.date);
            const tooltipLines = [formatValue(item.value)];
            return (
              <div key={item.date} className="analytics-chart-bar-wrap">
                <ChartColumnTooltip title={dateLabel} lines={tooltipLines}>
                  <div className="analytics-chart-bar-stack" style={{ height: `${CHART_BAR_AREA_PX}px` }}>
                    {showBarValues && item.value > 0 && (
                      <span className="analytics-chart-bar-value">{formatValue(item.value)}</span>
                    )}
                    <span
                      className={`analytics-chart-bar ${barClassName}`}
                      style={{ height: `${barHeight}px`, flex: "none" }}
                    />
                  </div>
                </ChartColumnTooltip>
                <span className="analytics-chart-label admin-stats-day-label">
                  {formatChartDay(item.date)}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function PageviewsChart({
  data,
  chartKey,
}: {
  data: AdminSiteStatsResponse["pageviews_by_day"];
  chartKey: string;
}) {
  const peak = useMemo(
    () => data.reduce((max, row) => Math.max(max, row.total, row.unique_visitors), 0),
    [data],
  );
  const periodTotal = useMemo(() => data.reduce((sum, row) => sum + row.total, 0), [data]);
  const periodUnique = useMemo(
    () => data.reduce((sum, row) => sum + row.unique_visitors, 0),
    [data],
  );
  const periodApp = useMemo(() => data.reduce((sum, row) => sum + row.app, 0), [data]);
  const periodDemo = useMemo(() => data.reduce((sum, row) => sum + row.demo, 0), [data]);
  const showBarValues = data.length <= 14;

  if (peak === 0) {
    return (
      <section className="card admin-stats-chart-card">
        <h3 className="admin-stats-chart-title">Просмотры страниц</h3>
        <p className="muted">
          Счётчик включён недавно — данные появятся после первых визитов на сайт.
        </p>
      </section>
    );
  }

  return (
    <section className="card admin-stats-chart-card admin-stats-pageviews-card" key={chartKey}>
      <h3 className="admin-stats-chart-title">Просмотры страниц</h3>
      <p className="admin-stats-chart-summary muted">
        За период: {periodTotal} просмотров, {periodUnique} уникальных по дням (ЛК {periodApp}, демо{" "}
        {periodDemo})
      </p>
      <div className="analytics-chart">
        <div
          className="analytics-chart-bars admin-stats-daily-bars admin-stats-pageview-bars"
          style={chartGridStyle(data.length)}
          role="img"
          aria-label="Просмотры и уникальные посетители по дням"
        >
          {data.map((row) => {
            const totalHeight = Math.max(4, Math.round((row.total / peak) * CHART_BAR_AREA_PX));
            const uvHeight =
              row.unique_visitors > 0
                ? Math.max(4, Math.round((row.unique_visitors / peak) * CHART_BAR_AREA_PX))
                : 0;
            const dateLabel = formatDate(row.date);
            const tooltipLines = [
              `Просмотров: ${row.total}`,
              `Уникальных: ${row.unique_visitors}`,
              `ЛК: ${row.app}`,
              `Демо: ${row.demo}`,
            ];
            return (
              <div key={row.date} className="analytics-chart-bar-wrap admin-stats-dual-bar-wrap">
                <ChartColumnTooltip title={dateLabel} lines={tooltipLines}>
                  <div className="admin-stats-dual-bars" style={{ height: `${CHART_BAR_AREA_PX}px` }}>
                    {showBarValues && row.total > 0 && (
                      <span className="analytics-chart-bar-value">{row.total}</span>
                    )}
                    <span
                      className="analytics-chart-bar analytics-chart-bar-run"
                      style={{ height: `${totalHeight}px`, flex: "none" }}
                    />
                  </div>
                  {uvHeight > 0 && (
                    <div
                      className="admin-stats-uv-bar"
                      style={{ height: `${uvHeight}px` }}
                      title={`Уникальных: ${row.unique_visitors}`}
                    />
                  )}
                </ChartColumnTooltip>
                <span className="analytics-chart-label admin-stats-day-label">
                  {formatChartDay(row.date)}
                </span>
              </div>
            );
          })}
        </div>
      </div>
      <div className="analytics-chart-legend admin-stats-pageview-legend">
        <span className="analytics-legend-item">
          <span className="analytics-legend-swatch analytics-legend-swatch-run" />
          Просмотры
        </span>
        <span className="analytics-legend-item">
          <span className="analytics-legend-swatch admin-stats-uv-swatch" />
          Уникальные посетители
        </span>
      </div>
    </section>
  );
}

function StatCard({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <article className="card admin-stats-card">
      <p className="admin-stats-card-value">{value}</p>
      <p className="admin-stats-card-label">{label}</p>
      {hint && <p className="muted admin-stats-card-hint">{hint}</p>}
    </article>
  );
}

function AdminStatsContent() {
  const [periodDays, setPeriodDays] = useState(7);
  const [data, setData] = useState<AdminSiteStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const loadSeq = useRef(0);

  const load = useCallback(async (days: number) => {
    const seq = ++loadSeq.current;
    setLoading(true);
    setError(null);
    try {
      const payload = await getAdminSiteStats(days);
      if (seq !== loadSeq.current) {
        return;
      }
      setData(payload);
    } catch (err) {
      if (seq !== loadSeq.current) {
        return;
      }
      setError(err instanceof Error ? err.message : "Не удалось загрузить статистику");
    } finally {
      if (seq === loadSeq.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void load(periodDays);
  }, [load, periodDays]);

  const overview = data?.overview;
  const chartKey = `${periodDays}-${data?.generated_at ?? "pending"}`;

  return (
    <AdminShell title="Статистика сайта">
      <AdminSubnav activePath="/admin/stats" />

      <div className="admin-stats-toolbar">
        <div className="admin-stats-periods" role="tablist" aria-label="Период">
          {PERIODS.map((period) => (
            <button
              key={period.days}
              type="button"
              className={periodDays === period.days ? "btn primary" : "btn secondary"}
              onClick={() => setPeriodDays(period.days)}
            >
              {period.label}
            </button>
          ))}
        </div>
        {data?.generated_at && (
          <span className="muted admin-stats-generated">Обновлено: {formatDateTime(data.generated_at)}</span>
        )}
      </div>

      {loading && <p className="muted">Загрузка…</p>}
      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!loading && !error && overview && data && (
        <>
          <section className="admin-stats-grid">
            <StatCard label="Просмотров за период" value={overview.pageviews_period} />
            <StatCard
              label="Уникальных посетителей"
              value={overview.unique_visitors_period}
              hint="Сумма по дням, один человек может учитываться несколько раз"
            />
            <StatCard label="Входов за период" value={overview.logins_period} hint="Успешные авторизации" />
            <StatCard label="Запросов на вход" value={overview.login_requests_period} hint="Кнопка «Войти»" />
            <StatCard label="Новых пользователей" value={overview.users_new_period} />
            <StatCard label="Новых привязок" value={overview.links_new_period} />
            <StatCard label="Учётных записей" value={overview.users_total} />
            <StatCard
              label="Публичных профилей"
              value={overview.users_profile_public}
              hint="Видны в поиске и рейтингах"
            />
            <StatCard
              label="Приватных профилей"
              value={overview.users_profile_private}
              hint="Скрыты из поиска и рейтингов"
            />
            <StatCard
              label="Активных за период"
              value={overview.users_active_period}
              hint="Заходили на сайт (last_login_at)"
            />
            <StatCard label="С согласием на данные" value={overview.users_with_consent} />
            <StatCard label="Привязок профилей" value={overview.platform_links_total} />
            <StatCard label="С хотя бы одной привязкой" value={overview.users_with_any_link} />
            <StatCard label="Во всех 3 системах" value={overview.users_with_all_three_links} />
            <StatCard label="Синхронизаций в очереди" value={overview.sync_jobs_active} />
          </section>

          <section className="card admin-stats-platform-links">
            <h2 className="section-title">Привязки по платформам</h2>
            <ul className="admin-stats-platform-list">
              {Object.entries(overview.links_by_platform).map(([code, count]) => (
                <li key={code}>
                  <PlatformBadge code={code} />
                  <span>
                    {platformCodeLabel(code)}: <strong>{count}</strong>
                  </span>
                </li>
              ))}
            </ul>
          </section>

          <section className="card admin-stats-data-core">
            <h2 className="section-title">Глобальное ядро данных</h2>
            <div className="admin-stats-grid admin-stats-grid-compact">
              <StatCard label="Участников в БД" value={overview.participants_total} />
              <StatCard label="Локаций" value={overview.locations_total} />
              <StatCard label="Мероприятий" value={overview.events_total} />
              <StatCard label="Результатов пробежек" value={overview.run_results_total} />
              <StatCard label="Синхронизаций всего" value={overview.sync_jobs_total} />
            </div>
          </section>

          <div className="admin-stats-charts">
            <PageviewsChart data={data.pageviews_by_day} chartKey={`pv-${chartKey}`} />
            <DailyBarChart
              title="Новые пользователи"
              data={data.users_new_by_day}
              ariaLabel="Новые пользователи по дням"
              chartKey={`users-${chartKey}`}
            />
            <DailyBarChart
              title="Новые привязки профилей"
              data={data.links_new_by_day}
              ariaLabel="Новые привязки по дням"
              barClassName="analytics-chart-bar-vol"
              chartKey={`links-${chartKey}`}
            />
            <DailyBarChart
              title="Входы на сайт"
              data={data.logins_by_day}
              ariaLabel="Успешные входы по дням"
              chartKey={`logins-${chartKey}`}
            />
            <DailyBarChart
              title="Запросы на вход"
              data={data.login_requests_by_day}
              ariaLabel="Запросы login-request по дням"
              barClassName="analytics-chart-bar-vol"
              chartKey={`login-req-${chartKey}`}
            />
          </div>

          <p className="muted admin-stats-footnote">
            Просмотры и уникальные посетители — из Redis-счётчика (посетитель = аккаунт или браузер в
            localStorage). Входы, регистрации и привязки — из базы данных.
          </p>
        </>
      )}
    </AdminShell>
  );
}

export function AdminStatsPage() {
  return <RequireAdmin>{() => <AdminStatsContent />}</RequireAdmin>;
}
