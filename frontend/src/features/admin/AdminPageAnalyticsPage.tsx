import { useCallback, useEffect, useRef, useState } from "react";
import { AdminShell } from "./AdminShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import { getAdminPageAnalytics, type PageAnalyticsEntity, type PageAnalyticsResponse } from "../../lib/api";
import { formatDate, formatDateTime } from "../../lib/format";
import { AdminSubnav } from "./AdminSubnav";

const PERIODS = [
  { label: "1 день", days: 1 },
  { label: "3 дня", days: 3 },
  { label: "7 дней", days: 7 },
  { label: "30 дней", days: 30 },
  { label: "90 дней", days: 90 },
  { label: "Год", days: 365 },
] as const;

// Канон page_type — _STATIC_PAGE_TYPES в backend/app/services/page_analytics_service.py.
// Добавили роут — добавьте строку и там, и здесь.
const PAGE_TYPE_LABELS: Record<string, string> = {
  landing: "Главная (старый лендинг, до портала)",
  login: "Вход (старый, до портала)",
  oauth_callback: "Возврат из OAuth",
  dashboard: "Дашборд",
  runs: "Пробежки",
  achievements: "Достижения",
  co_runners: "Встречи",
  volunteering: "Волонтёрство",
  maps: "Карта",
  history: "Моя история",
  profile: "Публичные профили",
  locations_index: "Локации (список)",
  location: "Локация (карточка)",
  location_events: "Локация (забеги)",
  ratings_hub: "Рейтинги (хаб)",
  ratings_runs: "Рейтинг: пробежки",
  ratings_volunteering: "Рейтинг: волонтёрство",
  ratings_locations: "Рейтинг: локации",
  ratings_wins: "Рейтинг: победы",
  ratings_win_locations: "Рейтинг: победные локации",
  share: "Поделиться",
  settings: "Настройки",
  about: "О проекте (старый, до портала)",
  demo: "Демо (все страницы)",
  portal_home: "Главная",
  portal_about: "О проекте",
  portal_blog: "Блог",
  blog_post_click: "Блог: переходы на посты",
  portal_login: "Вход",
  portal_map_lab: "Портал: карта (лаб)",
  admin: "Админка",
  backlog: "Бэклог",
  cabinet_preview: "Превью кабинета (демо)",
  sweep_hq: "Штаб обхода parkrun",
  redirect: "Редиректы и старые адреса кабинета",
  legacy_grafana: "Старые адреса Grafana",
  other: "Прочее (неизвестные адреса)",
};

function pageTypeLabel(pageType: string): string {
  return PAGE_TYPE_LABELS[pageType] ?? pageType;
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) {
    return "—";
  }
  if (seconds < 60) {
    return `${seconds} c`;
  }
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest > 0 ? `${minutes} мин ${rest} c` : `${minutes} мин`;
}

function StatsCells({ row }: { row: { views: number; unique_viewers: number; avg_duration_sec: number | null } }) {
  return (
    <>
      <td className="page-analytics-num">{row.views}</td>
      <td className="page-analytics-num">{row.unique_viewers}</td>
      <td className="page-analytics-num">{formatDuration(row.avg_duration_sec)}</td>
    </>
  );
}

function EntityTable({
  title,
  rows,
  entityHeader,
  showSelfViews,
  emptyText,
}: {
  title: string;
  rows: PageAnalyticsEntity[];
  entityHeader: string;
  showSelfViews?: boolean;
  emptyText: string;
}) {
  return (
    <section className="card">
      <h2 className="section-title">{title}</h2>
      {rows.length === 0 ? (
        <p className="muted">{emptyText}</p>
      ) : (
        <div className="table-scroll">
          <table className="data-table page-analytics-table">
            <thead>
              <tr>
                <th>#</th>
                <th>{entityHeader}</th>
                <th>Просмотры</th>
                <th>Уникальные</th>
                <th>Среднее время</th>
                {showSelfViews && <th>Самопросмотры</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={row.entity_key}>
                  <td className="muted">{index + 1}</td>
                  <td>{row.href ? <a href={row.href}>{row.label}</a> : row.label}</td>
                  <StatsCells row={row} />
                  {showSelfViews && <td className="page-analytics-num">{row.self_views}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** Период отчёта: быстрая кнопка (N дней) либо произвольный диапазон дат. */
type PeriodSelection = { days: number } | { from: string; to: string };

function AdminPageAnalyticsContent() {
  const [period, setPeriod] = useState<PeriodSelection>({ days: 30 });
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [data, setData] = useState<PageAnalyticsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const loadSeq = useRef(0);

  const load = useCallback(async (selection: PeriodSelection) => {
    const seq = ++loadSeq.current;
    setLoading(true);
    setError(null);
    try {
      const payload = await getAdminPageAnalytics(
        "days" in selection
          ? { periodDays: selection.days }
          : { dateFrom: selection.from || undefined, dateTo: selection.to || undefined },
      );
      if (seq !== loadSeq.current) {
        return;
      }
      setData(payload);
    } catch (err) {
      if (seq !== loadSeq.current) {
        return;
      }
      setError(err instanceof Error ? err.message : "Не удалось загрузить данные");
    } finally {
      if (seq === loadSeq.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void load(period);
  }, [load, period]);

  const activeDays = "days" in period ? period.days : null;
  const customApplied = !("days" in period);
  const canApplyCustom = customFrom !== "" || customTo !== "";

  const applyCustomRange = () => {
    if (canApplyCustom) {
      setPeriod({ from: customFrom, to: customTo });
    }
  };

  const totalViews = data?.sections.reduce((sum, section) => sum + section.views, 0) ?? 0;

  return (
    <AdminShell title="Популярность разделов">
      <AdminSubnav activePath="/admin/page-analytics" />

      <div className="admin-stats-toolbar">
        <div className="admin-stats-periods" role="tablist" aria-label="Период">
          {PERIODS.map((item) => (
            <button
              key={item.days}
              type="button"
              className={activeDays === item.days ? "btn primary" : "btn secondary"}
              onClick={() => {
                setCustomFrom("");
                setCustomTo("");
                setPeriod({ days: item.days });
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
        {data?.generated_at && (
          <span className="muted admin-stats-generated">Обновлено: {formatDateTime(data.generated_at)}</span>
        )}
      </div>

      <div className="page-analytics-range">
        <label className="filter-field">
          <span className="filter-field-label">С</span>
          <input
            className="filter-field-input"
            type="date"
            value={customFrom}
            max={customTo || undefined}
            onChange={(event) => setCustomFrom(event.target.value)}
          />
        </label>
        <label className="filter-field">
          <span className="filter-field-label">По</span>
          <input
            className="filter-field-input"
            type="date"
            value={customTo}
            min={customFrom || undefined}
            onChange={(event) => setCustomTo(event.target.value)}
          />
        </label>
        <button
          type="button"
          className={customApplied ? "btn primary" : "btn secondary"}
          disabled={!canApplyCustom}
          onClick={applyCustomRange}
        >
          Показать
        </button>
        {data && (
          <span className="muted page-analytics-range-label">
            Период: {formatDate(data.date_from)} — {formatDate(data.date_to)}
          </span>
        )}
      </div>

      {loading && <p className="muted">Загрузка…</p>}
      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!loading && !error && data && (
        <>
          {data.home_ab.length > 0 && (
            <section className="card">
              <h2 className="section-title">Показы вариантов главной</h2>
              <p className="muted">
                Сколько раз открылась главная каждого варианта АБ-теста и скольким посетителям.
                Пишется с 27.07.2026 — за более ранние периоды будут нули.
              </p>
              <div className="table-scroll">
                <table className="data-table page-analytics-table">
                  <thead>
                    <tr>
                      <th>Вариант</th>
                      <th>Показы</th>
                      <th>Посетители</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.home_ab.map((row) => (
                      <tr key={row.variant}>
                        <td>Вариант {row.variant}</td>
                        <td>{row.views}</td>
                        <td>{row.viewers}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          <section className="card">
            <h2 className="section-title">Разделы сайта</h2>
            {data.sections.length === 0 ? (
              <p className="muted">
                Данных за период пока нет — события копятся с момента включения аналитики, агрегаты
                пересчитываются раз в час.
              </p>
            ) : (
              <div className="table-scroll">
                <table className="data-table page-analytics-table">
                  <thead>
                    <tr>
                      <th>Раздел</th>
                      <th>Просмотры</th>
                      <th>Уникальные</th>
                      <th>Среднее время</th>
                      <th>Доля</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.sections.map((section) => (
                      <tr key={section.page_type}>
                        <td>{pageTypeLabel(section.page_type)}</td>
                        <StatsCells row={section} />
                        <td className="page-analytics-num muted">
                          {totalViews > 0 ? `${Math.round((section.views / totalViews) * 100)}%` : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <EntityTable
            title="Топ публичных профилей"
            rows={data.top_profiles}
            entityHeader="Профиль"
            showSelfViews
            emptyText="Просмотров публичных профилей за период не было."
          />
          <EntityTable
            title="Топ локаций"
            rows={data.top_locations}
            entityHeader="Локация"
            emptyText="Просмотров страниц локаций за период не было."
          />

          <p className="muted admin-stats-footnote">
            Уникальные — сумма дневных уникальных посетителей (один человек в разные дни учитывается
            несколько раз). Среднее время — по просмотрам с зафиксированной длительностью.
            Самопросмотры (владелец открыл собственный профиль) входят в «Просмотры» и показаны отдельно.
            Агрегаты обновляются раз в час; сырые события хранятся 90 дней, дневные итоги — бессрочно.
          </p>
        </>
      )}
    </AdminShell>
  );
}

export function AdminPageAnalyticsPage() {
  return <RequireAdmin>{() => <AdminPageAnalyticsContent />}</RequireAdmin>;
}
