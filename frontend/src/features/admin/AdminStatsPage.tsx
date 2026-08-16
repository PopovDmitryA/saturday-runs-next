import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { AdminShell } from "./AdminShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import { PlatformBadge } from "../../components/PlatformBadge";
import { ChartColumnTooltip } from "../../components/ChartColumnTooltip";
import {
  getAdminSiteStats,
  getAdminUsersGeography,
  type AdminLinkCombinationRow,
  type AdminSiteStatsResponse,
  type AdminUsersGeographyResponse,
} from "../../lib/api";
import {
  formatChartDay,
  formatDate,
  formatDateTime,
  formatInt,
  formatStatValue,
  platformCodeLabel,
} from "../../lib/format";
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
  const formatValue = valueLabel ?? ((value: number) => formatInt(value));
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
        За период: {formatInt(periodTotal)} просмотров, {formatInt(periodUnique)} уникальных по дням (ЛК{" "}
        {formatInt(periodApp)}, демо {formatInt(periodDemo)})
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
              `Просмотров: ${formatInt(row.total)}`,
              `Уникальных: ${formatInt(row.unique_visitors)}`,
              `ЛК: ${formatInt(row.app)}`,
              `Демо: ${formatInt(row.demo)}`,
            ];
            return (
              <div key={row.date} className="analytics-chart-bar-wrap admin-stats-dual-bar-wrap">
                <ChartColumnTooltip title={dateLabel} lines={tooltipLines}>
                  <div className="admin-stats-dual-bars" style={{ height: `${CHART_BAR_AREA_PX}px` }}>
                    {showBarValues && row.total > 0 && (
                      <span className="analytics-chart-bar-value">{formatInt(row.total)}</span>
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
      <p className="admin-stats-card-value">{formatStatValue(value)}</p>
      <p className="admin-stats-card-label">{label}</p>
      {hint && <p className="muted admin-stats-card-hint">{hint}</p>}
    </article>
  );
}

// Наборы привязок: человек попадает ровно в одну строку, поэтому суммы по
// строкам сходятся с числом учётных записей — это и позволяет сравнивать
// «только 5 вёрст» с «5 вёрст + parkrun» напрямую.
function LinkCombinations({
  rows,
  withoutLinks,
  usersTotal,
}: {
  rows: AdminLinkCombinationRow[];
  withoutLinks: number;
  usersTotal: number;
}) {
  const [showEmpty, setShowEmpty] = useState(false);
  const emptyRows = rows.filter((row) => row.users === 0).length;
  const visible = showEmpty ? rows : rows.filter((row) => row.users > 0);
  const share = (users: number) =>
    usersTotal > 0 ? `${((users / usersTotal) * 100).toFixed(1)}%` : "—";

  return (
    <div className="admin-stats-combos">
      <div className="admin-stats-combos-head">
        <h3 className="admin-stats-chart-title">Наборы привязок</h3>
        {emptyRows > 0 && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowEmpty((v) => !v)}>
            {showEmpty ? "Скрыть пустые" : `Показать пустые (${formatInt(emptyRows)})`}
          </button>
        )}
      </div>
      <p className="muted admin-stats-combos-lead">
        Набор точный: человек с 5 вёрстами и S95 считается только в строке «5 вёрст + с95».
      </p>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Систем</th>
              <th>Набор</th>
              <th>Пользователей</th>
              <th>Доля</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <tr key={row.codes.join("+")} className={row.users === 0 ? "muted" : undefined}>
                <td>{row.codes.length}</td>
                <td>
                  <span className="admin-stats-combo-codes">
                    {row.codes.map((code) => (
                      <PlatformBadge code={code} key={code} />
                    ))}
                  </span>
                </td>
                <td>{formatInt(row.users)}</td>
                <td className="muted">{share(row.users)}</td>
              </tr>
            ))}
            <tr>
              <td>0</td>
              <td className="muted">без привязок</td>
              <td>{formatInt(withoutLinks)}</td>
              <td className="muted">{share(withoutLinks)}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Сколько строк показываем в срезе «Откуда люди» до нажатия «Показать все».
const GEO_PREVIEW_ROWS = 12;

type GeoSort = "users" | "new";

// «Откуда наши люди»: регистрации по городам и площадкам. Отвечает на вопрос
// «где сарафанка уже работает» — город и площадка берутся из домашней локации
// пользователя, той же, что он видит у себя в кабинете.
function GeographySection({ periodDays }: { periodDays: number }) {
  const [data, setData] = useState<AdminUsersGeographyResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<GeoSort>("users");
  const [expanded, setExpanded] = useState(false);
  const loadSeq = useRef(0);

  useEffect(() => {
    const seq = ++loadSeq.current;
    setLoading(true);
    setError(null);
    getAdminUsersGeography(periodDays)
      .then((payload) => {
        if (seq === loadSeq.current) {
          setData(payload);
        }
      })
      .catch((err) => {
        if (seq === loadSeq.current) {
          setError(err instanceof Error ? err.message : "Не удалось загрузить срез по городам");
        }
      })
      .finally(() => {
        if (seq === loadSeq.current) {
          setLoading(false);
        }
      });
  }, [periodDays]);

  const needle = search.trim().toLowerCase();
  const matches = useCallback(
    (parts: (string | null)[]) =>
      !needle || parts.filter(Boolean).join(" ").toLowerCase().includes(needle),
    [needle],
  );
  const bySort = useCallback(
    (a: { users: number; users_new_period: number }, b: { users: number; users_new_period: number }) =>
      sort === "new"
        ? b.users_new_period - a.users_new_period || b.users - a.users
        : b.users - a.users || b.users_new_period - a.users_new_period,
    [sort],
  );

  const cities = useMemo(
    () => (data?.cities ?? []).filter((row) => matches([row.city, row.region])).sort(bySort),
    [data, matches, bySort],
  );
  const locations = useMemo(
    () =>
      (data?.locations ?? [])
        .filter((row) => matches([row.name, row.city, row.region]))
        .sort(bySort),
    [data, matches, bySort],
  );

  const limit = expanded ? Number.MAX_SAFE_INTEGER : GEO_PREVIEW_ROWS;
  const hiddenRows = Math.max(cities.length - limit, 0) + Math.max(locations.length - limit, 0);

  return (
    <section className="card admin-stats-geo">
      <h2 className="section-title">Откуда наши люди</h2>
      <p className="muted admin-stats-geo-lead">
        Город и площадка — из домашней локации пользователя (ручной выбор в настройках либо
        автовыбор по пробежкам). У кого пробежек в базе нет, дома нет тоже.
      </p>

      {loading && <p className="muted">Считаем по протоколам…</p>}
      {error && <p className="form-error">{error}</p>}

      {!loading && !error && data && (
        <>
          <div className="admin-stats-grid admin-stats-grid-compact">
            <StatCard
              label="С известным домом"
              value={data.users_with_home}
              hint={`из ${formatInt(data.users_total)} учётных записей`}
            />
            <StatCard
              label="Новых за период"
              value={data.users_new_with_home}
              hint={`всего новых: ${formatInt(data.users_new_period)}`}
            />
            <StatCard label="Городов" value={data.cities_total} />
            <StatCard label="Площадок" value={data.locations_total} />
            <StatCard
              label="Без дома"
              value={data.users_without_home}
              hint={`из них без привязок: ${formatInt(data.users_without_links)}`}
            />
          </div>

          <div className="admin-stats-geo-toolbar">
            <input
              className="input admin-stats-geo-search"
              type="search"
              placeholder="Город, регион или площадка…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            <div className="admin-stats-geo-sort" role="group" aria-label="Сортировка">
              <button
                type="button"
                className={sort === "users" ? "btn primary btn-sm" : "btn secondary btn-sm"}
                onClick={() => setSort("users")}
              >
                По всем
              </button>
              <button
                type="button"
                className={sort === "new" ? "btn primary btn-sm" : "btn secondary btn-sm"}
                onClick={() => setSort("new")}
              >
                По новым за период
              </button>
            </div>
            {(hiddenRows > 0 || expanded) && (
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setExpanded((v) => !v)}>
                {expanded ? "Свернуть" : `Показать все (+${formatInt(hiddenRows)})`}
              </button>
            )}
          </div>

          <div className="admin-stats-geo-tables">
            <div className="admin-stats-geo-table">
              <h3 className="admin-stats-chart-title">Города</h3>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Город</th>
                      <th>Регион</th>
                      <th>Пользователей</th>
                      <th>Новых за период</th>
                      <th>Площадок</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cities.length === 0 && (
                      <tr>
                        <td colSpan={5} className="muted">
                          Ничего не найдено
                        </td>
                      </tr>
                    )}
                    {cities.slice(0, limit).map((row) => (
                      <tr key={`${row.city}|${row.region ?? ""}`}>
                        <td>{row.city}</td>
                        <td className="muted">{row.region ?? "—"}</td>
                        <td>{formatInt(row.users)}</td>
                        <td>{row.users_new_period > 0 ? `+${formatInt(row.users_new_period)}` : "—"}</td>
                        <td>{formatInt(row.locations)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="admin-stats-geo-table">
              <h3 className="admin-stats-chart-title">Площадки</h3>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Площадка</th>
                      <th>Город</th>
                      <th>Пользователей</th>
                      <th>Новых за период</th>
                    </tr>
                  </thead>
                  <tbody>
                    {locations.length === 0 && (
                      <tr>
                        <td colSpan={4} className="muted">
                          Ничего не найдено
                        </td>
                      </tr>
                    )}
                    {locations.slice(0, limit).map((row) => (
                      <tr key={row.identity_key}>
                        <td>
                          {row.slug ? (
                            <a
                              href={`/locations/${row.slug}`}
                              target="_blank"
                              rel="noreferrer"
                              className="admin-platform-link"
                            >
                              {row.name}
                            </a>
                          ) : (
                            row.name
                          )}
                        </td>
                        <td className="muted">{row.city ?? "—"}</td>
                        <td>{formatInt(row.users)}</td>
                        <td>{row.users_new_period > 0 ? `+${formatInt(row.users_new_period)}` : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </>
      )}
    </section>
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
                    {platformCodeLabel(code)}: <strong>{formatInt(count)}</strong>
                  </span>
                </li>
              ))}
            </ul>

            <LinkCombinations
              rows={data.link_combinations}
              withoutLinks={data.users_without_links}
              usersTotal={overview.users_total}
            />
          </section>

          <GeographySection periodDays={periodDays} />

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
