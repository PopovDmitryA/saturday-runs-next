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
 *
 * Карточка «Это вы?» — воронка от нажатия на человека из протоколов до
 * привязки (27.09.2026). Этапы пишет сервер по анонимному ключу токена, без
 * пользователя и участника: гость нажал → вошёл → привязал, вошедший нажал →
 * привязал.
 */
import { useEffect, useState } from "react";
import { RequireAdmin } from "../../components/RequireAdmin";
import { getAdminSearchLog, type AdminSearchLogResponse, type SearchClaimFunnel } from "../../lib/api";
import { formatDate, formatDateTime, platformCodeLabel } from "../../lib/format";
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
  person: "участник сайта",
  participant: "человек из протоколов",
  none: "никуда не перешли",
};

const CLICK_KINDS = ["page", "location", "person", "participant", "none"] as const;

/**
 * Ошибки привязки «Это вы?» по кодам ответа — словами, код в скобках. 409 у
 * этой ручки — и «профиль занят другим», и «у вас уже есть профиль этой
 * системы»: по коду их не различить.
 */
const CLAIM_ERROR_LABELS: Record<string, string> = {
  "400": "токен повреждён",
  "401": "сессия кончилась",
  "403": "нет согласия",
  "404": "профиль не найден",
  "409": "занят или уже есть профиль системы",
  "410": "выдача устарела",
  "422": "нет данных",
  "429": "лимит запросов",
};

function claimErrorLabel(status: string): string {
  const label = CLAIM_ERROR_LABELS[status] ?? (Number(status) >= 500 ? "сбой сервера" : "другая ошибка");
  return `${label} (${status})`;
}

/** Цель клика в ленте: у человека из протоколов — «система#номер строки». */
function clickedTargetLabel(kind: string | null, target: string): string {
  if (kind !== "participant") return target;
  const match = /^([a-z0-9_]*)#(\d+)$/.exec(target);
  if (!match) return target;
  return `${match[1] ? platformCodeLabel(match[1]) : "система не указана"} · строка ${match[2]}`;
}

function percent(part: number, total: number): string {
  if (total === 0) return "—";
  return `${Math.round((part / total) * 100)}%`;
}

/**
 * Воронка «Это вы?». Гости и вошедшие — отдельно: у гостя между нажатием и
 * привязкой вход, и главная потеря обычно там. Проценты — от «нажали».
 */
function ClaimFunnelCard({ funnel }: { funnel: SearchClaimFunnel }) {
  const failed = Object.entries(funnel.failed_by_status).sort((a, b) => b[1] - a[1]);
  const rows = [
    { who: "Гости", opened: funnel.guest_opened, loggedIn: funnel.guest_logged_in, linked: funnel.guest_linked },
    // Вошедшему входить не нужно — между нажатием и привязкой шага нет.
    { who: "Вошедшие", opened: funnel.authed_opened, loggedIn: null, linked: funnel.authed_linked },
  ];
  const withShare = (value: number, opened: number) => (
    <>
      {value}
      <span className="muted"> · {percent(value, opened)}</span>
    </>
  );
  return (
    <section className="card">
      <h2 className="section-title">«Это вы?» — от нажатия до привязки</h2>
      <p className="muted">
        Человек нажал на себя среди людей из протоколов. Гостю нужно войти — после входа сайт сам предлагает
        привязать этого же человека. Проценты — от «нажали». Единица — строка выдачи, на которую нажали:
        повторные нажатия той же строки не множатся; без имён и аккаунтов.
      </p>
      <TableWrap>
        <table className="data-table">
          <thead>
            <tr>
              <th>Кто</th>
              <th>Нажали</th>
              <th>Вошли</th>
              <th>Привязали</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.who}>
                <td>{row.who}</td>
                <td>{row.opened}</td>
                <td>{row.loggedIn === null ? "—" : withShare(row.loggedIn, row.opened)}</td>
                <td>{withShare(row.linked, row.opened)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
      <p className="muted">
        «Это не я»: {funnel.declined} · ошибки привязки:{" "}
        {failed.length === 0
          ? "нет"
          : failed.map(([status, count]) => `${claimErrorLabel(status)} — ${count}`).join(", ")}
      </p>
    </section>
  );
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

  const clicksTotal = data ? CLICK_KINDS.reduce((sum, kind) => sum + (data.clicks_by_kind[kind] ?? 0), 0) : 0;

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
            {CLICK_KINDS.map((kind) => (
              <div key={kind} className="stat-card">
                <span className="stat-value">{data.clicks_by_kind[kind] ?? 0}</span>
                <span className="stat-label">
                  {CLICK_LABELS[kind]} · {percent(data.clicks_by_kind[kind] ?? 0, clicksTotal)}
                </span>
              </div>
            ))}
          </div>

          <section className="card">
            <h2 className="section-title">Искали и никуда не перешли</h2>
            <p className="muted">
              Главный список: человек посмотрел выдачу и закрыл окно, ушёл «Назад» или закрыл вкладку. Обычно
              это недостающий синоним (nav/siteNav.ts) или страница, которой нет. Фамилии здесь нормальны —
              человек мог просто посмотреть цифры однофамильцев; нажатие на себя («Это вы?») сюда не попадает.
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

          {data.claim_funnel && <ClaimFunnelCard funnel={data.claim_funnel} />}

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
                          {row.clicked_target && (
                            <div className="muted">{clickedTargetLabel(row.clicked_kind, row.clicked_target)}</div>
                          )}
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
