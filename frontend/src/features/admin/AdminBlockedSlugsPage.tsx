import { useCallback, useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { ConfirmModal } from "../../components/ConfirmModal";
import { RequireAdmin } from "../../components/RequireAdmin";
import {
  createAdminBlockedSlug,
  deleteAdminBlockedSlug,
  listAdminBlockedSlugs,
  type BlockedSlugItem,
} from "../../lib/api";
import { formatDateTime } from "../../lib/format";
import { AdminSubnav } from "./AdminSubnav";

function AdminBlockedSlugsContent() {
  const [items, setItems] = useState<BlockedSlugItem[]>([]);
  const [systemSlugs, setSystemSlugs] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const [slug, setSlug] = useState("");
  const [comment, setComment] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [formSuccess, setFormSuccess] = useState<string | null>(null);

  const [confirmDelete, setConfirmDelete] = useState<BlockedSlugItem | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await listAdminBlockedSlugs();
      setItems(response.items);
      setSystemSlugs(response.system_slugs ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить блок-лист");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = async () => {
    setFormError(null);
    setFormSuccess(null);
    const trimmed = slug.trim();
    if (!trimmed) {
      setFormError("Укажите ссылку (никнейм) для резерва");
      return;
    }
    setActionLoading("create");
    try {
      const created = await createAdminBlockedSlug({ slug: trimmed, comment });
      setFormSuccess(`Ссылка зарезервирована: /${created.slug}`);
      setSlug("");
      setComment("");
      await load();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Не удалось зарезервировать ссылку");
    } finally {
      setActionLoading(null);
    }
  };

  const handleDelete = async () => {
    if (!confirmDelete) {
      return;
    }
    setActionLoading(`delete-${confirmDelete.id}`);
    try {
      await deleteAdminBlockedSlug(confirmDelete.id);
      setConfirmDelete(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить запись");
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <AppShell title="Резерв ссылок" activePath="/admin">
      <AdminSubnav activePath="/admin/profile-slugs" />

      <section className="card admin-abuse-form">
        <h2 className="section-title">Зарезервировать ссылку</h2>
        <p className="muted admin-abuse-form-hint">
          Ник для публичного профиля (/users/&lt;ник&gt;), который нельзя занять. Пользователь
          с такой ссылкой в настройках получит отказ. Правила формата — как у обычной ссылки:
          латиница, цифры, дефис и подчёркивание, 3–30 символов. Хранится в нижнем регистре.
        </p>
        <label className="field">
          <span className="field-label">Ссылка (ник)</span>
          <input
            className="input"
            type="text"
            value={slug}
            onChange={(event) => setSlug(event.target.value)}
            placeholder="например, engirunner"
          />
        </label>
        <label className="field">
          <span className="field-label">Комментарий</span>
          <input
            className="input"
            type="text"
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder="Почему зарезервировали (необязательно)"
          />
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
            disabled={actionLoading === "create"}
            onClick={() => void handleCreate()}
          >
            {actionLoading === "create" ? "Сохранение…" : "Зарезервировать"}
          </button>
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
          <h2 className="section-title">Зарезервированные ссылки ({items.length})</h2>
          {items.length === 0 ? (
            <p className="muted">Пока ничего не зарезервировано.</p>
          ) : (
            <div className="table-scroll">
              <table className="data-table admin-abuse-table">
                <thead>
                  <tr>
                    <th>Ссылка</th>
                    <th>Комментарий</th>
                    <th>Создана</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <code>/{item.slug}</code>
                      </td>
                      <td>{item.comment || "—"}</td>
                      <td>{formatDateTime(item.created_at)}</td>
                      <td className="admin-abuse-actions">
                        <button
                          type="button"
                          className="btn btn-ghost"
                          disabled={actionLoading !== null}
                          onClick={() => setConfirmDelete(item)}
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

      {!loading && !error && systemSlugs.length > 0 && (
        <section className="card">
          <h2 className="section-title">Системные ссылки ({systemSlugs.length})</h2>
          <p className="muted admin-abuse-form-hint">
            Служебные пути приложения (роуты, спецстраницы). Заблокированы в коде и недоступны
            пользователям всегда — здесь показаны для наглядности, удалить нельзя.
          </p>
          <div className="admin-system-slugs">
            {systemSlugs.map((slug) => (
              <code key={slug} className="admin-system-slug">/{slug}</code>
            ))}
          </div>
        </section>
      )}

      <ConfirmModal
        open={confirmDelete !== null}
        title="Убрать резерв?"
        confirmLabel="Удалить"
        confirmLoading={actionLoading?.startsWith("delete-") ?? false}
        onConfirm={() => void handleDelete()}
        onCancel={() => setConfirmDelete(null)}
      >
        {confirmDelete
          ? `Убрать резерв ссылки /${confirmDelete.slug}? После этого её снова можно будет занять.`
          : ""}
      </ConfirmModal>
    </AppShell>
  );
}

export function AdminBlockedSlugsPage() {
  return <RequireAdmin>{() => <AdminBlockedSlugsContent />}</RequireAdmin>;
}
