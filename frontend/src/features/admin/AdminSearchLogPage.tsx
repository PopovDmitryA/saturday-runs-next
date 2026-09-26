/**
 * Журнал поиска по сайту (решение Дмитрия 23.09.2026: «полезно для статистики
 * и аналитики, чтобы понять, что ищут»).
 *
 * Главное здесь — «искали и никуда не перешли» (ревью 25.09.2026): человек
 * что-то увидел, но нужного там не было. Пустая выдача — лишь частный случай:
 * «погода» с Погодаевыми пустой не считалась, хотя страницу погоды человек так
 * и не нашёл. Это готовый список синонимов, которых не хватает в
 * nav/siteNav.ts (и в словаре городов на сервере), и разделов, которых на
 * сайте нет. Журнал анонимный: ни пользователя, ни посетителя в записи нет.
 */
import { useEffect, useState } from "react";
import { RequireAdmin } from "../../components/RequireAdmin";
import { getAdminSearchLog, type AdminSearchLogResponse } from "../../lib/api";
import { formatDate, formatDateTime } from "../../lib/format";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { AdminShell } from "./AdminShell";
import { AdminSubnav } from "./AdminSubnav";

const PERIODS = [
  { label: "7 дней", days: 7 },
  { label: "30 дней", days: 30 },
  { label: "90 дней", days: 90 },
] as const;

const CLICK_LABELS: Record<string, string> = {
  page: "страница сайта",
  location: "локация",
  person: "участник",
  none: "никуда не перешли",
};

function percent(part: number, total: number): string {
  if (total === 0) return "—";
  return `${Math.round((part / total) * 100)}%`;
}

function AdminSearchLogContent() {
  const [days, setDays] = useState<number>(30);
  const [data, setData] = useState<AdminSearchLogResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getAdminSearchLog(days)
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Не удалось загрузить журнал поиска");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [days]);

  const clicksTotal = data
    ? data.clicks_by_kind.page + data.clicks_by_kind.location + data.clicks_by_kind.person + data.clicks_by_kind.none
    : 0;

  return (
    <AdminShell title="Поиск по сайту">
      <AdminSubnav activePath="/admin/search" />

      <div className="admin-stats-toolbar">
        <div className="admin-stats-periods" role="tablist" aria-label="Период">
          {PERIODS.map((item) => (
            <button
              key={item.days}
              type="button"
              className={days === item.days ? "btn primary" : "btn secondary"}
              onClick={() => setDays(item.days)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {loading && <p className="muted">Загрузка…</p>}
      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {data && !loading && (
        <>
          <div className="stats-grid">
            <div className="stat-card">
              <span className="stat-value">{data.total}</span>
              <span className="stat-label">поисков</span>
            </div>
            <div className="stat-card">
              <span className="stat-value">{data.zero_result_total}</span>
              <span className="stat-label">без результатов · {percent(data.zero_result_total, data.total)}</span>
            </div>
            {(["page", "location", "person", "none"] as const).map((kind) => (
              <div key={kind} className="stat-card">
                <span className="stat-value">{data.clicks_by_kind[kind]}</span>
                <span className="stat-label">
                  {CLICK_LABELS[kind]} · {percent(data.clicks_by_kind[kind], clicksTotal)}
                </span>
              </div>
            ))}
          </div>

          <section className="card">
            <h2 className="section-title">Искали и никуда не перешли</h2>
            <p className="muted">
              Главный список: человек посмотрел выдачу и закрыл окно, ушёл «Назад» или закрыл вкладку. Обычно
              это недостающий синоним (nav/siteNav.ts) или страница, которой нет. Фамилии здесь нормальны —
              людей из протоколов открыть нельзя, по ним переходов и не бывает.
            </p>
            {data.no_click_queries.length === 0 ? (
              <p className="muted">За период таких запросов нет.</p>
            ) : (
              <TableWrap>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Запрос</th>
                      <th>Раз</th>
                      <th>Из них пусто</th>
                      <th>Последний</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.no_click_queries.map((row) => (
                      <tr key={row.query}>
                        <td>{row.query}</td>
                        <td>{row.count}</td>
                        <td>{row.zero_results_count || "—"}</td>
                        <td>{formatDateTime(row.last_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </section>

          <section className="card">
            <h2 className="section-title">Не нашлось ничего</h2>
            <p className="muted">
              Кандидаты в синонимы (nav/siteNav.ts) и в новые разделы. Если запрос — фамилия, человека просто
              нет в протоколах.
            </p>
            {data.zero_result_queries.length === 0 ? (
              <p className="muted">За период таких запросов нет.</p>
            ) : (
              <TableWrap>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Запрос</th>
                      <th>Раз</th>
                      <th>Последний</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.zero_result_queries.map((row) => (
                      <tr key={row.query}>
                        <td>{row.query}</td>
                        <td>{row.count}</td>
                        <td>{formatDateTime(row.last_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </section>

          <section className="card">
            <h2 className="section-title">Частые запросы</h2>
            {data.top_queries.length === 0 ? (
              <p className="muted">За период поисков не было.</p>
            ) : (
              <TableWrap>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Запрос</th>
                      <th>Раз</th>
                      <th>Без результатов</th>
                      <th>С переходом</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.top_queries.map((row) => (
                      <tr key={row.query}>
                        <td>{row.query}</td>
                        <td>{row.count}</td>
                        <td>{row.zero_results_count || "—"}</td>
                        <td>{percent(row.clicks, row.count)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </section>

          {data.daily.length > 0 && (
            <section className="card">
              <h2 className="section-title">По дням</h2>
              <TableWrap>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>День</th>
                      <th>Поисков</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.daily.map((row) => (
                      <tr key={row.date}>
                        <td>{formatDate(row.date)}</td>
                        <td>{row.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            </section>
          )}

          <section className="card">
            <h2 className="section-title">Последние поиски</h2>
            {data.recent.length === 0 ? (
              <p className="muted">Пока пусто.</p>
            ) : (
              <TableWrap>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Когда</th>
                      <th>Запрос</th>
                      <th>Страниц</th>
                      <th>Локаций</th>
                      <th>Людей</th>
                      <th>Переход</th>
                      <th>Кто</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.recent.map((row, index) => (
                      <tr key={`${row.created_at}-${index}`}>
                        <td>{formatDateTime(row.created_at)}</td>
                        <td>
                          {row.query}
                          {row.corrected_query && <span className="muted"> → {row.corrected_query}</span>}
                        </td>
                        <td>{row.pages_found}</td>
                        <td>{row.locations_found}</td>
                        <td>{row.people_found}</td>
                        <td>
                          {row.clicked_kind ? `${CLICK_LABELS[row.clicked_kind] ?? row.clicked_kind}` : "—"}
                          {row.clicked_target && <div className="muted">{row.clicked_target}</div>}
                        </td>
                        <td>
                          {row.is_authed ? "вошёл" : "гость"} · {row.is_mobile ? "телефон" : "компьютер"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </section>
        </>
      )}
    </AdminShell>
  );
}

export function AdminSearchLogPage() {
  return <RequireAdmin>{() => <AdminSearchLogContent />}</RequireAdmin>;
}
