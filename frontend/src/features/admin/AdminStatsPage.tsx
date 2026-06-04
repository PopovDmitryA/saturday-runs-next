import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import { PlatformBadge } from "../../components/PlatformBadge";
import { ChartColumnTooltip } from "../../components/ChartColumnTooltip";
import { getAdminSiteStats, type AdminSiteStatsResponse } from "../../lib/api";
import { formatDate, formatDateTime, platformCodeLabel } from "../../lib/format";
import { AdminSubnav } from "./AdminSubnav";

const PERIODS = [
  { label: "7 дней", days: 7 },
  { label: "30 дней", days: 30 },
  { label: "90 дней", days: 90 },
] as const;

type DayPoint = { date: string; value: number };

function maxValue(items: DayPoint[]): number {
  return items.reduce((max, item) => Math.max(max, item.value), 0);
}

function sumValues(items: DayPoint[]): number {
  return items.reduce((sum, item) => sum + item.value, 0);
}

function DailyBarChart({
  title,
  data,
  ariaLabel,
  barClassName = "analytics-chart-bar-run",
  valueLabel,
}: {
  title: string;
  data: DayPoint[];
  ariaLabel: string;
  barClassName?: string;
  valueLabel?: (value: number) => string;
}) {
  const peak = maxValue(data);
  const periodTotal = sumValues(data);
  const formatValue = valueLabel ?? ((value: number) => String(value));

  if (peak === 0) {
    return (
      <section className="card admin-stats-chart-card">
        <h3 className="admin-stats-chart-title">{title}</h3>
        <p className="muted">За выбранный период данных пока нет.</p>
      </section>
    );
  }

  return (
    <section className="card admin-stats-chart-card">
      <h3 className="admin-stats-chart-title">{title}</h3>
      <p className="admin-stats-chart-summary muted">За период: {formatValue(periodTotal)}</p>
      <div className="analytics-chart">
        <div className="analytics-chart-bars admin-stats-daily-bars" role="img" aria-label={ariaLabel}>
          {data.map((item) => {
            const height = Math.max(4, Math.round((item.value / peak) * 100));
            const dateLabel = formatDate(item.date);
            const tooltipLines = [formatValue(item.value)];
            return (
              <div key={item.date} className="analytics-chart-bar-wrap">
                <ChartColumnTooltip title={dateLabel} lines={tooltipLines}>
                  <div className="analytics-chart-bar-stack" style={{ height: `${height}%` }}>
                    {item.value > 0 && (
                      <span className="analytics-chart-bar-value">{formatValue(item.value)}</span>
                    )}
                    <span className={`analytics-chart-bar ${barClassName}`} style={{ flex: 1 }} />
                  </div>
                </ChartColumnTooltip>
                <span className="analytics-chart-label admin-stats-day-label">{dateLabel.slice(0, 5)}</span>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function PageviewsChart({ data }: { data: AdminSiteStatsResponse["pageviews_by_day"] }) {
  const peak = useMemo(
    () => data.reduce((max, row) => Math.max(max, row.total, row.unique_visitors), 0),
    [data],
  );
  const periodTotal = useMemo(
    () => data.reduce((sum, row) => sum + row.total, 0),
    [data],
  );
  const periodUnique = useMemo(
    () => data.reduce((sum, row) => sum + row.unique_visitors, 0),
    [data],
  );
  const periodApp = useMemo(
    () => data.reduce((sum, row) => sum + row.app, 0),
    [data],
  );
  const periodDemo = useMemo(
    () => data.reduce((sum, row) => sum + row.demo, 0),
    [data],
  );

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
    <section className="card admin-stats-chart-card">
      <h3 className="admin-stats-chart-title">Просмотры страниц</h3>
      <p className="admin-stats-chart-summary muted">
        За период: {periodTotal} просмотров, {periodUnique} уникальных по дням (ЛК {periodApp}, демо{" "}
        {periodDemo})
      </p>
      <div className="analytics-chart">
        <div
          className="analytics-chart-bars admin-stats-daily-bars admin-stats-pageview-bars"
          role="img"
          aria-label="Просмотры и уникальные посетители по дням"
        >
          {data.map((row) => {
            const stackHeight = Math.max(4, Math.round((row.total / peak) * 100));
            const uvHeight =
              row.unique_visitors > 0
                ? Math.max(4, Math.round((row.unique_visitors / peak) * 100))
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
                  <div className="admin-stats-dual-bars" style={{ height: `${stackHeight}%` }}>
                    {row.total > 0 && (
                      <span className="analytics-chart-bar-value">{row.total}</span>
                    )}
                    <span className="analytics-chart-bar analytics-chart-bar-run" style={{ flex: 1 }} />
                  </div>
                  {uvHeight > 0 && (
                    <div
                      className="admin-stats-uv-bar"
                      style={{ height: `${uvHeight}%` }}
                      title={`Уникальных: ${row.unique_visitors}`}
                    />
                  )}
                </ChartColumnTooltip>
                <span className="analytics-chart-label admin-stats-day-label">{dateLabel.slice(0, 5)}</span>
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
  const [periodDays, setPeriodDays] = useState(30);
  const [data, setData] = useState<AdminSiteStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (days: number) => {
    setLoading(true);
    setError(null);
    try {
      setData(await getAdminSiteStats(days));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить статистику");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(periodDays);
  }, [load, periodDays]);

  const overview = data?.overview;

  return (
    <AppShell title="Статистика сайта" activePath="/admin">
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
            <StatCard label="Учётных записей" value={overview.users_total} />
            <StatCard
              label="Новых за период"
              value={data.users_new_by_day.reduce((sum, item) => sum + item.value, 0)}
            />
            <StatCard
              label="Активных за период"
              value={overview.users_active_period}
              hint="Заходили на сайт (last_login_at)"
            />
            <StatCard label="С согласием на данные" value={overview.users_with_consent} />
            <StatCard label="Подписаны на новости" value={overview.users_news_subscribed} />
            <StatCard label="Привязок профилей" value={overview.platform_links_total} />
            <StatCard
              label="Пользователей с привязками"
              value={overview.users_with_any_link}
            />
            <StatCard
              label="Во всех 3 системах"
              value={overview.users_with_all_three_links}
            />
            <StatCard
              label="Запросов на вход"
              value={overview.login_requests_period}
              hint="Кнопка «Войти через Telegram»"
            />
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
            <PageviewsChart data={data.pageviews_by_day} />
            <DailyBarChart
              title="Новые пользователи"
              data={data.users_new_by_day}
              ariaLabel="Новые пользователи по дням"
            />
            <DailyBarChart
              title="Новые привязки профилей"
              data={data.links_new_by_day}
              ariaLabel="Новые привязки по дням"
              barClassName="analytics-chart-bar-vol"
            />
            <DailyBarChart
              title="Входы на сайт"
              data={data.logins_by_day}
              ariaLabel="Успешные входы по дням"
            />
            <DailyBarChart
              title="Запросы на вход"
              data={data.login_requests_by_day}
              ariaLabel="Запросы login-request по дням"
              barClassName="analytics-chart-bar-vol"
            />
          </div>

          <p className="muted admin-stats-footnote">
            Просмотры и уникальные посетители считаются с момента включения счётчика (посетитель = аккаунт
            или браузер в localStorage). Сумма «уникальных по дням» может считать одного человека несколько
            раз, если он заходил в разные дни. Регистрации и привязки — из базы данных.
          </p>
        </>
      )}
    </AppShell>
  );
}

export function AdminStatsPage() {
  return <RequireAdmin>{() => <AdminStatsContent />}</RequireAdmin>;
}
