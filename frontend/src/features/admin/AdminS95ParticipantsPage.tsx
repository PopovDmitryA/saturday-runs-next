import { useCallback, useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import { AdminSubnav } from "./AdminSubnav";
import { listAdminS95Participants, type AdminS95ParticipantListItem } from "../../lib/api";
import { formatDateTime } from "../../lib/format";

function AdminS95ParticipantsContent() {
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [items, setItems] = useState<AdminS95ParticipantListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (search: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await listAdminS95Participants(search);
      setItems(response.items);
      setTotal(response.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить участников");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(query);
  }, [load, query]);

  return (
    <AppShell title="S95 — участники">
      <AdminSubnav activePath="/admin/s95-participants" />
      <section className="admin-section">
        <header className="admin-section-header">
          <div>
            <h1>Участники S95</h1>
            <p className="muted">Профили из реестра участников, отсортированы по дате последнего обновления.</p>
          </div>
          <form
            className="admin-search-form"
            onSubmit={(event) => {
              event.preventDefault();
              setQuery(draft.trim());
            }}
          >
            <input
              type="search"
              placeholder="ID, имя, штрихкод, локация…"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
            />
            <button type="submit" className="btn secondary">
              Найти
            </button>
          </form>
        </header>

        {error ? <p className="error-text">{error}</p> : null}
        {loading ? <p className="muted">Загрузка…</p> : null}

        {!loading && !error ? (
          <>
            <p className="muted admin-list-meta">Всего: {total}</p>
            <div className="admin-table-wrap">
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Имя</th>
                    <th>Штрихкод</th>
                    <th>Собирается в</th>
                    <th>Обновлён</th>
                    <th>Статус</th>
                  </tr>
                </thead>
                <tbody>
                  {items.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="muted">
                        Участники не найдены
                      </td>
                    </tr>
                  ) : (
                    items.map((item) => (
                      <tr key={item.id}>
                        <td>
                          {item.profile_url ? (
                            <a href={item.profile_url} target="_blank" rel="noreferrer" className="admin-platform-link">
                              {item.external_user_id}
                            </a>
                          ) : (
                            item.external_user_id
                          )}
                        </td>
                        <td>{item.display_name ?? "—"}</td>
                        <td>{item.barcode_id ?? "—"}</td>
                        <td>
                          {item.planning_location ? (
                            <>
                              {item.planning_location}
                              {item.planning_location_seen_at ? (
                                <div className="muted admin-cell-sub">
                                  {formatDateTime(item.planning_location_seen_at)}
                                </div>
                              ) : null}
                            </>
                          ) : (
                            "—"
                          )}
                        </td>
                        <td>{item.fetched_at ? formatDateTime(item.fetched_at) : "—"}</td>
                        <td>{item.sync_status}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </>
        ) : null}
      </section>
    </AppShell>
  );
}

export function AdminS95ParticipantsPage() {
  return (
    <RequireAdmin>
      <AdminS95ParticipantsContent />
    </RequireAdmin>
  );
}
