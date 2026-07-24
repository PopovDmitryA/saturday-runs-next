import { Fragment, useCallback, useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { ConfirmModal } from "../../components/ConfirmModal";
import { RequireAdmin } from "../../components/RequireAdmin";
import {
  BACKLOG_STATUS_LABELS,
  BACKLOG_TYPE_LABELS,
  deleteAdminBacklogCard,
  listAdminBacklogCards,
  listAdminBacklogVotes,
  updateAdminBacklogCardStatus,
  type BacklogCardAdmin,
  type BacklogCardStatus,
  type BacklogVoteAdminList,
} from "../backlog/backlogApi";
import "../backlog/backlog.css";
import { AdminSubnav } from "./AdminSubnav";

const STATUS_OPTIONS: BacklogCardStatus[] = ["pending", "in_progress", "rejected", "done"];

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
}

function AdminBacklogContent() {
  const [items, setItems] = useState<BacklogCardAdmin[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<BacklogCardAdmin | null>(null);

  // Раскрытие списка «кто голосовал» под строкой карточки.
  const [expandedVotes, setExpandedVotes] = useState<string | null>(null);
  const [votes, setVotes] = useState<Record<string, BacklogVoteAdminList>>({});
  const [votesLoading, setVotesLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await listAdminBacklogCards();
      setItems(response.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить бэклог");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleStatusChange = async (cardId: string, status: BacklogCardStatus) => {
    setSaving(cardId);
    setError(null);
    try {
      const updated = await updateAdminBacklogCardStatus(cardId, { status });
      // Статус влияет на сортировку (done уходит в подвал) — перезагружаем.
      setItems((prev) => prev.map((card) => (card.id === updated.id ? updated : card)));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить статус");
    } finally {
      setSaving(null);
    }
  };

  const handleDelete = async () => {
    if (!confirmDelete) return;
    setSaving(confirmDelete.id);
    try {
      await deleteAdminBacklogCard(confirmDelete.id);
      setConfirmDelete(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить карточку");
    } finally {
      setSaving(null);
    }
  };

  const toggleVotes = async (cardId: string) => {
    if (expandedVotes === cardId) {
      setExpandedVotes(null);
      return;
    }
    setExpandedVotes(cardId);
    if (!votes[cardId]) {
      setVotesLoading(true);
      try {
        const response = await listAdminBacklogVotes(cardId);
        setVotes((prev) => ({ ...prev, [cardId]: response }));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Не удалось загрузить голоса");
      } finally {
        setVotesLoading(false);
      }
    }
  };

  return (
    <AppShell title="Бэклог" activePath="/admin">
      <AdminSubnav activePath="/admin/backlog" />

      {loading && <p className="muted">Загрузка…</p>}
      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!loading && !error && (
        <section className="card">
          <h2 className="section-title">Карточки бэклога ({items.length})</h2>
          {items.length === 0 ? (
            <p className="muted">Пока карточек нет.</p>
          ) : (
            <div className="table-scroll">
              <table className="data-table admin-abuse-table backlog-admin-table">
                <thead>
                  <tr>
                    <th>Карточка</th>
                    <th>Тип / категория</th>
                    <th>Автор</th>
                    <th>Голоса</th>
                    <th>Комм.</th>
                    <th>Статус</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {items.map((card) => (
                    <Fragment key={card.id}>
                      <tr className={card.status === "done" ? "backlog-admin-row-done" : undefined}>
                        <td>
                          <b>{card.title}</b>
                          <p className="muted" style={{ margin: "2px 0 0", fontSize: "0.82rem" }}>
                            {card.description.length > 140 ? `${card.description.slice(0, 140)}…` : card.description}
                          </p>
                          <p className="muted" style={{ margin: "2px 0 0", fontSize: "0.78rem" }}>
                            {formatDate(card.created_at)}
                          </p>
                        </td>
                        <td>
                          {BACKLOG_TYPE_LABELS[card.type]} · {card.category}
                        </td>
                        <td>
                          {card.author_display_name}
                          {card.is_anonymous && (
                            <span
                              className="badge backlog-badge-anon-warn"
                              title="Скрыт от остальных пользователей под «Аноним» — не упоминайте имя в публичном ответе"
                            >
                              аноним для всех
                            </span>
                          )}
                        </td>
                        <td>
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            title="Показать, кто голосовал"
                            onClick={() => void toggleVotes(card.id)}
                          >
                            {card.score} ({card.upvotes}/{card.downvotes}) {expandedVotes === card.id ? "▲" : "▼"}
                          </button>
                        </td>
                        <td className="num">{card.comment_count}</td>
                        <td>
                          <select
                            className="input"
                            value={card.status}
                            disabled={saving === card.id}
                            onChange={(event) =>
                              void handleStatusChange(card.id, event.target.value as BacklogCardStatus)
                            }
                          >
                            {STATUS_OPTIONS.map((status) => (
                              <option key={status} value={status}>
                                {BACKLOG_STATUS_LABELS[status]}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td className="admin-abuse-actions">
                          <button
                            type="button"
                            className="btn btn-ghost"
                            disabled={saving === card.id}
                            onClick={() => setConfirmDelete(card)}
                          >
                            Удалить
                          </button>
                        </td>
                      </tr>
                      {expandedVotes === card.id && (
                        <tr>
                          <td colSpan={7}>
                            {votesLoading && !votes[card.id] ? (
                              <p className="muted">Загрузка голосов…</p>
                            ) : (
                              <div className="backlog-admin-votes">
                                <div>
                                  <b className="backlog-admin-votes-up">За ({votes[card.id]?.upvotes.length ?? 0})</b>
                                  {votes[card.id]?.upvotes.length ? (
                                    <ul className="plain-list">
                                      {votes[card.id]!.upvotes.map((v) => (
                                        <li key={v.author_handle}>
                                          <a href={`/users/${v.author_handle}`} target="_blank" rel="noreferrer">
                                            {v.author_display_name}
                                          </a>
                                        </li>
                                      ))}
                                    </ul>
                                  ) : (
                                    <p className="muted">—</p>
                                  )}
                                </div>
                                <div>
                                  <b className="backlog-admin-votes-down">
                                    Против ({votes[card.id]?.downvotes.length ?? 0})
                                  </b>
                                  {votes[card.id]?.downvotes.length ? (
                                    <ul className="plain-list">
                                      {votes[card.id]!.downvotes.map((v) => (
                                        <li key={v.author_handle}>
                                          <a href={`/users/${v.author_handle}`} target="_blank" rel="noreferrer">
                                            {v.author_display_name}
                                          </a>
                                        </li>
                                      ))}
                                    </ul>
                                  ) : (
                                    <p className="muted">—</p>
                                  )}
                                </div>
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      <ConfirmModal
        open={confirmDelete !== null}
        title="Удалить карточку?"
        confirmLabel="Удалить"
        variant="danger"
        confirmLoading={saving === confirmDelete?.id}
        onConfirm={() => void handleDelete()}
        onCancel={() => setConfirmDelete(null)}
      >
        {confirmDelete ? `Удалить «${confirmDelete.title}» из бэклога вместе с голосами и комментариями?` : ""}
      </ConfirmModal>
    </AppShell>
  );
}

export function AdminBacklogPage() {
  return <RequireAdmin>{() => <AdminBacklogContent />}</RequireAdmin>;
}
