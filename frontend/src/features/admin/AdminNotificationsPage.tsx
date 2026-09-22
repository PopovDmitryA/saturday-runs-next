import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "./AdminShell";
import { AdminSubnav } from "./AdminSubnav";
import { RequireAdmin } from "../../components/RequireAdmin";
import { getAdminNotifications, type AdminNotificationsResponse } from "../../lib/api";
import { formatDateTime } from "../../lib/format";

const PERIODS = [
  { label: "Сутки", days: 1 },
  { label: "Неделя", days: 7 },
  { label: "Месяц", days: 30 },
] as const;

const STATUS_LABELS: Record<string, string> = {
  queued: "в очереди",
  sent: "доставлено",
  failed: "не доставлено",
  skipped: "пропущено",
};

const KIND_LABELS: Record<string, string> = {
  runs: "Пробежка",
  backlog: "Бэклог",
  backlog_new_cards: "Новые карточки",
  volunteer_signup: "Волонтёрство",
  test: "Проверка",
};

const CHANNEL_LABELS: Record<string, string> = { telegram: "Telegram", vk: "VK", email: "Почта" };

function label(map: Record<string, string>, key: string | null): string {
  if (!key) return "—";
  return map[key] ?? key;
}

function CountList({ title, items, map }: { title: string; items: { key: string; count: number }[]; map: Record<string, string> }) {
  return (
    <div className="admin-notify-count">
      <p className="admin-notify-count-title">{title}</p>
      {items.length === 0 ? (
        <p className="muted">—</p>
      ) : (
        <ul>
          {items.map((item) => (
            <li key={item.key}>
              <span>{label(map, item.key)}</span>
              <b className="num">{item.count}</b>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function AdminNotificationsContent() {
  const [periodDays, setPeriodDays] = useState<number>(7);
  const [status, setStatus] = useState<string>("");
  const [kind, setKind] = useState<string>("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<AdminNotificationsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const limit = 50;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await getAdminNotifications({ periodDays, status: status || null, kind: kind || null, limit, offset }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить журнал");
    } finally {
      setLoading(false);
    }
  }, [periodDays, status, kind, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  const pages = data ? Math.max(1, Math.ceil(data.items_total / limit)) : 1;
  const page = Math.floor(offset / limit) + 1;

  return (
    <AdminShell title="Уведомления">
      <AdminSubnav activePath="/admin/notifications" />

      <section className="card">
        <div className="admin-notify-toolbar">
          <div className="backlog-segmented">
            {PERIODS.map((item) => (
              <button
                key={item.days}
                type="button"
                className={periodDays === item.days ? "backlog-segmented-btn active" : "backlog-segmented-btn"}
                onClick={() => {
                  setPeriodDays(item.days);
                  setOffset(0);
                }}
              >
                {item.label}
              </button>
            ))}
          </div>
          <select
            className="input"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setOffset(0);
            }}
          >
            <option value="">Все статусы</option>
            {Object.entries(STATUS_LABELS).map(([key, value]) => (
              <option key={key} value={key}>
                {value}
              </option>
            ))}
          </select>
          <select
            className="input"
            value={kind}
            onChange={(event) => {
              setKind(event.target.value);
              setOffset(0);
            }}
          >
            <option value="">Все виды</option>
            {Object.entries(KIND_LABELS).map(([key, value]) => (
              <option key={key} value={key}>
                {value}
              </option>
            ))}
          </select>
        </div>

        {loading && !data && <p className="muted">Загрузка…</p>}
        {error && <p className="error-text">{error}</p>}

        {data && (
          <>
            <div className="admin-notify-counts">
              <CountList title="Подписаны, по каналам" items={data.subscribers_by_channel} map={CHANNEL_LABELS} />
              <CountList title="По статусам" items={data.by_status} map={STATUS_LABELS} />
              <CountList title="По видам" items={data.by_kind} map={KIND_LABELS} />
              <CountList title="Доставлено через" items={data.by_channel} map={CHANNEL_LABELS} />
            </div>

            <div className="table-scroll">
              <table className="data-table admin-notify-table">
                <thead>
                  <tr>
                    <th>Когда</th>
                    <th>Кому</th>
                    <th>Вид</th>
                    <th>Заголовок</th>
                    <th>Статус</th>
                    <th>Канал</th>
                    <th>Ошибка</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((item) => (
                    <tr key={item.id} className={`admin-notify-row-${item.status}`}>
                      <td className="num">{formatDateTime(item.created_at)}</td>
                      <td>
                        {item.user_serial_id ? (
                          <a href={`/admin/users?q=${item.user_serial_id}`}>{item.user_label}</a>
                        ) : (
                          item.user_label
                        )}
                      </td>
                      <td>{label(KIND_LABELS, item.kind)}</td>
                      <td>{item.title}</td>
                      <td>
                        <span className={`admin-notify-status admin-notify-status-${item.status}`}>
                          {label(STATUS_LABELS, item.status)}
                          {item.attempts > 1 ? ` ×${item.attempts}` : ""}
                        </span>
                      </td>
                      <td>{label(CHANNEL_LABELS, item.channel)}</td>
                      <td className="admin-notify-error">{item.error ?? ""}</td>
                    </tr>
                  ))}
                  {data.items.length === 0 && (
                    <tr>
                      <td colSpan={7} className="muted">
                        За выбранный период уведомлений не было.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {pages > 1 && (
              <div className="actions-row admin-notify-pager">
                <button type="button" className="btn btn-sm" disabled={page <= 1} onClick={() => setOffset(offset - limit)}>
                  ← Назад
                </button>
                <span className="muted">
                  {page} / {pages}
                </span>
                <button type="button" className="btn btn-sm" disabled={page >= pages} onClick={() => setOffset(offset + limit)}>
                  Вперёд →
                </button>
              </div>
            )}
          </>
        )}
      </section>
    </AdminShell>
  );
}

export function AdminNotificationsPage() {
  return (
    <RequireAdmin>
      <AdminNotificationsContent />
    </RequireAdmin>
  );
}
