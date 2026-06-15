import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import { AdminSubnav } from "./AdminSubnav";
import { listAdminUsers, type AdminUserListItem } from "../../lib/api";
import { formatDateTime, platformCodeLabel } from "../../lib/format";
import { authProviderLabel, telegramProfileUrl, userLoginLines } from "./adminUserDisplay";

function platformCell(user: AdminUserListItem, code: string) {
  const link = user.platform_links.find((item) => item.platform_code === code);
  if (!link) {
    return <span className="muted">—</span>;
  }
  const counts = (
    <span className="admin-platform-counts muted"> ({link.run_count}/{link.volunteer_count})</span>
  );
  const label = link.display_name?.trim();
  if (!label) {
    return (
      <a href={link.external_url} target="_blank" rel="noreferrer" className="admin-platform-link muted">
        Профиль
        {counts}
      </a>
    );
  }
  return (
    <a href={link.external_url} target="_blank" rel="noreferrer" className="admin-platform-link">
      {label}
      {counts}
    </a>
  );
}

const USERS_PAGE_SIZE = 100;

function AdminUsersContent() {
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<AdminUserListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const offset = (page - 1) * USERS_PAGE_SIZE;
  const totalPages = useMemo(() => Math.max(1, Math.ceil(total / USERS_PAGE_SIZE)), [total]);

  const load = useCallback(async (search: string, pageOffset: number) => {
    setLoading(true);
    setError(null);
    try {
      const response = await listAdminUsers(search, USERS_PAGE_SIZE, pageOffset);
      setItems(response.items);
      setTotal(response.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить пользователей");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const trimmed = draft.trim();
      setQuery((prev) => {
        if (trimmed !== prev) {
          setPage(1);
        }
        return trimmed;
      });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [draft]);

  useEffect(() => {
    void load(query, (page - 1) * USERS_PAGE_SIZE);
  }, [load, query, page]);

  return (
    <AppShell title="Пользователи" activePath="/admin">
      <div className="admin-users-page">
      <AdminSubnav activePath="/admin/users" />

      <section className="card admin-users-card">
        <div className="admin-users-toolbar">
          <input
            className="input admin-users-search"
            type="search"
            placeholder="Почта, VK/Яндекс, Telegram, имя в ЛК или ФИО в 5 вёрst/S95/parkrun…"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
          <span className="muted admin-users-count">
            {total === 0
              ? "Найдено: 0"
              : `Показано ${offset + 1}–${offset + items.length} из ${total}`}
          </span>
        </div>

        {!loading && !error && total > USERS_PAGE_SIZE && (
          <div className="admin-users-pagination">
            <button
              type="button"
              className="btn secondary btn-sm"
              disabled={page <= 1}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
            >
              Назад
            </button>
            <span className="muted admin-users-pagination-label">
              Страница {page} из {totalPages}
            </span>
            <button
              type="button"
              className="btn secondary btn-sm"
              disabled={page >= totalPages}
              onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
            >
              Вперёд
            </button>
          </div>
        )}

        {loading && <p className="muted">Загрузка…</p>}
        {error && (
          <div className="card error">
            <p>{error}</p>
          </div>
        )}

        {!loading && !error && (
          <div className="table-scroll">
            <table className="data-table admin-users-table">
              <thead>
                <tr>
                  <th>Telegram</th>
                  <th>{platformCodeLabel("five_verst")}</th>
                  <th>{platformCodeLabel("s95")}</th>
                  <th>{platformCodeLabel("parkrun")}</th>
                  <th>Пробежки</th>
                  <th>Волонт.</th>
                  <th>Регистрация</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.length === 0 && (
                  <tr>
                    <td colSpan={8} className="muted">
                      Пользователи не найдены
                    </td>
                  </tr>
                )}
                {items.map((user) => {
                  const logins = userLoginLines(user);
                  const tgUrl = telegramProfileUrl(user);
                  return (
                    <tr key={user.id}>
                      <td className="admin-users-telegram">
                        <ul className="admin-users-login-list">
                          {logins.length === 0 ? (
                            <li className="muted">—</li>
                          ) : (
                            logins.map((login) => (
                              <li key={`${login.provider}-${login.external_id}`}>
                                <span className="admin-users-login-provider">
                                  {authProviderLabel(login.provider)}
                                </span>
                                {login.provider === "telegram" && tgUrl ? (
                                  <a href={tgUrl} target="_blank" rel="noreferrer">
                                    {login.label}
                                  </a>
                                ) : (
                                  <span>{login.label}</span>
                                )}
                              </li>
                            ))
                          )}
                        </ul>
                      </td>
                      <td>{platformCell(user, "five_verst")}</td>
                      <td>{platformCell(user, "s95")}</td>
                      <td>{platformCell(user, "parkrun")}</td>
                      <td>{user.total_runs ?? "—"}</td>
                      <td>{user.total_volunteering ?? "—"}</td>
                      <td title={formatDateTime(user.created_at)}>
                        {formatDateTime(user.created_at)}
                      </td>
                      <td>
                        <a className="btn secondary btn-sm" href={`/admin/users/${user.id}/preview`}>
                          Просмотр
                        </a>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
      </div>
    </AppShell>
  );
}

export function AdminUsersPage() {
  return <RequireAdmin>{() => <AdminUsersContent />}</RequireAdmin>;
}
