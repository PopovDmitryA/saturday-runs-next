import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { ConfirmModal } from "../../components/ConfirmModal";
import { RequireAdmin } from "../../components/RequireAdmin";
import {
  createAdminBlogPost,
  deleteAdminBlogPost,
  listAdminBlogPosts,
  updateAdminBlogPost,
  type BlogPostAdmin,
  type BlogPostAdminPayload,
} from "../../lib/api";
import { AdminSubnav } from "./AdminSubnav";

type Draft = {
  title: string;
  teaser: string;
  telegram_url: string;
  topic: string;
  published_at: string; // yyyy-mm-dd для <input type="date">, пусто = сейчас
  is_published: boolean;
};

const EMPTY_DRAFT: Draft = {
  title: "",
  teaser: "",
  telegram_url: "",
  topic: "",
  published_at: "",
  is_published: true,
};

function toDraft(post: BlogPostAdmin): Draft {
  return {
    title: post.title,
    teaser: post.teaser,
    telegram_url: post.telegram_url,
    topic: post.topic ?? "",
    published_at: post.published_at.slice(0, 10),
    is_published: post.is_published,
  };
}

function toPayload(draft: Draft): BlogPostAdminPayload {
  return {
    title: draft.title,
    teaser: draft.teaser,
    telegram_url: draft.telegram_url,
    topic: draft.topic.trim() || null,
    published_at: draft.published_at || null,
    is_published: draft.is_published,
  };
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

function AdminBlogContent() {
  const [items, setItems] = useState<BlogPostAdmin[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [formSuccess, setFormSuccess] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<BlogPostAdmin | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await listAdminBlogPosts();
      setItems(response.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить посты");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const knownTopics = useMemo(
    () => Array.from(new Set(items.map((post) => post.topic).filter(Boolean))) as string[],
    [items],
  );

  const startEdit = (post: BlogPostAdmin) => {
    setEditingId(post.id);
    setDraft(toDraft(post));
    setFormError(null);
    setFormSuccess(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const resetForm = () => {
    setEditingId(null);
    setDraft(EMPTY_DRAFT);
    setFormError(null);
    setFormSuccess(null);
  };

  const handleSave = async () => {
    setFormError(null);
    setFormSuccess(null);
    setSaving(true);
    try {
      if (editingId) {
        await updateAdminBlogPost(editingId, toPayload(draft));
        setFormSuccess("Пост обновлён");
      } else {
        await createAdminBlogPost(toPayload(draft));
        setFormSuccess("Пост добавлен");
      }
      setEditingId(null);
      setDraft(EMPTY_DRAFT);
      await load();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Не удалось сохранить пост");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!confirmDelete) {
      return;
    }
    setSaving(true);
    try {
      await deleteAdminBlogPost(confirmDelete.id);
      setConfirmDelete(null);
      if (editingId === confirmDelete.id) {
        resetForm();
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить пост");
    } finally {
      setSaving(false);
    }
  };

  const togglePublished = async (post: BlogPostAdmin) => {
    setSaving(true);
    try {
      await updateAdminBlogPost(post.id, {
        ...toPayload(toDraft(post)),
        is_published: !post.is_published,
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить пост");
    } finally {
      setSaving(false);
    }
  };

  return (
    <AppShell title="Блог" activePath="/admin">
      <AdminSubnav activePath="/admin/blog" />

      <section className="card admin-abuse-form">
        <h2 className="section-title">
          {editingId ? "Редактировать пост" : "Добавить пост"}
        </h2>
        <p className="muted admin-abuse-form-hint">
          Затравка показывается на главной и на странице «Блог», клик по карточке ведёт на пост
          в Telegram-канале (там полный текст и комментарии). Обрывайте затравку на интересном
          месте — она и должна уводить читателя в канал.
        </p>
        <label className="field">
          <span className="field-label">Заголовок</span>
          <input
            className="input"
            type="text"
            value={draft.title}
            maxLength={200}
            onChange={(event) => setDraft({ ...draft, title: event.target.value })}
            placeholder="Например: 500 суббот Ивана из Кузьминок"
          />
        </label>
        <label className="field">
          <span className="field-label">Затравка</span>
          <textarea
            className="input"
            rows={4}
            value={draft.teaser}
            onChange={(event) => setDraft({ ...draft, teaser: event.target.value })}
            placeholder="Первые пара абзацев поста — до самого интересного места…"
          />
        </label>
        <label className="field">
          <span className="field-label">Ссылка на пост в Telegram</span>
          <input
            className="input"
            type="text"
            value={draft.telegram_url}
            onChange={(event) => setDraft({ ...draft, telegram_url: event.target.value })}
            placeholder="https://t.me/popov_way/123"
          />
        </label>
        <label className="field">
          <span className="field-label">Тема</span>
          <input
            className="input"
            type="text"
            list="admin-blog-topics"
            value={draft.topic}
            maxLength={64}
            onChange={(event) => setDraft({ ...draft, topic: event.target.value })}
            placeholder="Например: Люди / Рекорды / Локации (необязательно)"
          />
          <datalist id="admin-blog-topics">
            {knownTopics.map((topic) => (
              <option key={topic} value={topic} />
            ))}
          </datalist>
        </label>
        <label className="field">
          <span className="field-label">Дата публикации</span>
          <input
            className="input"
            type="date"
            value={draft.published_at}
            onChange={(event) => setDraft({ ...draft, published_at: event.target.value })}
          />
        </label>
        <label className="login-consent-field">
          <input
            type="checkbox"
            checked={draft.is_published}
            onChange={(event) => setDraft({ ...draft, is_published: event.target.checked })}
          />
          <span>Опубликован (виден на сайте)</span>
        </label>
        {formError && (
          <div className="profile-form-error" role="alert">
            <p>{formError}</p>
          </div>
        )}
        {formSuccess && <p className="success-text">{formSuccess}</p>}
        <div className="actions-row">
          <button
            type="button"
            className="btn primary"
            disabled={saving}
            onClick={() => void handleSave()}
          >
            {saving ? "Сохранение…" : editingId ? "Сохранить" : "Добавить"}
          </button>
          {editingId && (
            <button type="button" className="btn btn-ghost" disabled={saving} onClick={resetForm}>
              Отменить правку
            </button>
          )}
        </div>
      </section>

      {loading && <p className="muted">Загрузка…</p>}
      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!loading && !error && (
        <section className="card">
          <h2 className="section-title">Посты ({items.length})</h2>
          {items.length === 0 ? (
            <p className="muted">Постов пока нет — добавьте первый в форме выше.</p>
          ) : (
            <div className="table-scroll">
              <table className="data-table admin-abuse-table">
                <thead>
                  <tr>
                    <th>Заголовок</th>
                    <th>Тема</th>
                    <th>Дата</th>
                    <th>Клики</th>
                    <th>Статус</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {items.map((post) => (
                    <tr key={post.id}>
                      <td>
                        <a href={post.telegram_url} target="_blank" rel="noreferrer">
                          {post.title}
                        </a>
                      </td>
                      <td>{post.topic || "—"}</td>
                      <td>{formatDate(post.published_at)}</td>
                      <td className="num">{post.clicks_count}</td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-ghost"
                          disabled={saving}
                          title={post.is_published ? "Скрыть с сайта" : "Показать на сайте"}
                          onClick={() => void togglePublished(post)}
                        >
                          {post.is_published ? "✅ виден" : "🚫 скрыт"}
                        </button>
                      </td>
                      <td className="admin-abuse-actions">
                        <button
                          type="button"
                          className="btn btn-ghost"
                          disabled={saving}
                          onClick={() => startEdit(post)}
                        >
                          Ред.
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost"
                          disabled={saving}
                          onClick={() => setConfirmDelete(post)}
                        >
                          Удалить
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      <ConfirmModal
        open={confirmDelete !== null}
        title="Удалить пост?"
        confirmLabel="Удалить"
        confirmLoading={saving}
        onConfirm={() => void handleDelete()}
        onCancel={() => setConfirmDelete(null)}
      >
        {confirmDelete
          ? `Удалить «${confirmDelete.title}» из блога? Пост в Telegram-канале не пострадает.`
          : ""}
      </ConfirmModal>
    </AppShell>
  );
}

export function AdminBlogPage() {
  return <RequireAdmin>{() => <AdminBlogContent />}</RequireAdmin>;
}
