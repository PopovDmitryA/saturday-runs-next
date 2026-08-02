import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "./AdminShell";
import { ConfirmModal } from "../../components/ConfirmModal";
import { RequireAdmin } from "../../components/RequireAdmin";
import {
  createAdminRelease,
  deleteAdminRelease,
  listAdminReleases,
  updateAdminRelease,
  type ReleaseAdmin,
  type ReleaseAdminPayload,
  type ReleaseNextVersions,
} from "../../lib/api";
import { AdminSubnav } from "./AdminSubnav";

type Draft = {
  version: string;
  title: string;
  body: string;
  released_at: string; // yyyy-mm-dd для <input type="date">, пусто = сегодня
  is_published: boolean;
};

const EMPTY_DRAFT: Draft = {
  version: "",
  title: "",
  body: "",
  released_at: "",
  is_published: false,
};

function toDraft(release: ReleaseAdmin): Draft {
  return {
    version: release.version,
    title: release.title,
    body: release.body,
    released_at: release.released_at,
    is_published: release.is_published,
  };
}

function toPayload(draft: Draft): ReleaseAdminPayload {
  return {
    version: draft.version,
    title: draft.title,
    body: draft.body,
    released_at: draft.released_at || null,
    is_published: draft.is_published,
  };
}

function formatDate(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

function AdminReleasesContent() {
  const [items, setItems] = useState<ReleaseAdmin[]>([]);
  const [nextVersions, setNextVersions] = useState<ReleaseNextVersions | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [formSuccess, setFormSuccess] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<ReleaseAdmin | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await listAdminReleases();
      setItems(response.items);
      setNextVersions(response.next_versions);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить релизы");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const startEdit = (release: ReleaseAdmin) => {
    setEditingId(release.id);
    setDraft(toDraft(release));
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
        await updateAdminRelease(editingId, toPayload(draft));
        setFormSuccess("Релиз обновлён");
      } else {
        await createAdminRelease(toPayload(draft));
        setFormSuccess("Релиз добавлен");
      }
      setEditingId(null);
      setDraft(EMPTY_DRAFT);
      await load();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Не удалось сохранить релиз");
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
      await deleteAdminRelease(confirmDelete.id);
      setConfirmDelete(null);
      if (editingId === confirmDelete.id) {
        resetForm();
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить релиз");
    } finally {
      setSaving(false);
    }
  };

  const togglePublished = async (release: ReleaseAdmin) => {
    setSaving(true);
    try {
      await updateAdminRelease(release.id, {
        ...toPayload(toDraft(release)),
        is_published: !release.is_published,
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить релиз");
    } finally {
      setSaving(false);
    }
  };

  return (
    <AdminShell title="Релизы">
      <AdminSubnav activePath="/admin/releases" />

      <section className="card admin-abuse-form">
        <h2 className="section-title">
          {editingId ? "Редактировать релиз" : "Добавить релиз"}
        </h2>
        <p className="muted admin-abuse-form-hint">
          Записи создаются при деплое скрытыми — проверьте текст и откройте релиз на сайте.
          Формат текста: абзацы разделяются пустой строкой, пункты — строками «- …».
          Скрытый или удалённый релиз оставляет пропуск в опубликованных номерах — это
          нормально.
        </p>
        {nextVersions && !editingId && (
          <p className="muted admin-abuse-form-hint">
            Текущая версия: <b>{nextVersions.current}</b>. Следующая — X: {nextVersions.major},
            Y: {nextVersions.minor}, Z: {nextVersions.patch}, fix: {nextVersions.fix}.
          </p>
        )}
        <label className="field">
          <span className="field-label">Версия</span>
          <input
            className="input"
            type="text"
            value={draft.version}
            maxLength={32}
            onChange={(event) => setDraft({ ...draft, version: event.target.value })}
            placeholder="Например: 2.4.0 или 2.4.0-fix1"
          />
        </label>
        <label className="field">
          <span className="field-label">Заголовок</span>
          <input
            className="input"
            type="text"
            value={draft.title}
            maxLength={200}
            onChange={(event) => setDraft({ ...draft, title: event.target.value })}
            placeholder="Например: Страница обновлений и футер"
          />
        </label>
        <label className="field">
          <span className="field-label">Что вошло в релиз</span>
          <textarea
            className="input"
            rows={7}
            value={draft.body}
            onChange={(event) => setDraft({ ...draft, body: event.target.value })}
            placeholder={"Короткое вступление.\n\n- Первый пункт\n- Второй пункт"}
          />
        </label>
        <label className="field">
          <span className="field-label">Дата релиза</span>
          <input
            className="input"
            type="date"
            value={draft.released_at}
            onChange={(event) => setDraft({ ...draft, released_at: event.target.value })}
          />
        </label>
        <label className="login-consent-field">
          <input
            type="checkbox"
            checked={draft.is_published}
            onChange={(event) => setDraft({ ...draft, is_published: event.target.checked })}
          />
          <span>Показан на странице «Обновления»</span>
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
          <h2 className="section-title">Релизы ({items.length})</h2>
          {items.length === 0 ? (
            <p className="muted">Релизов пока нет — добавьте первый в форме выше.</p>
          ) : (
            <div className="table-scroll">
              <table className="data-table admin-abuse-table">
                <thead>
                  <tr>
                    <th>Версия</th>
                    <th>Заголовок</th>
                    <th>Дата</th>
                    <th>Статус</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {items.map((release) => (
                    <tr key={release.id}>
                      <td className="num">{release.version}</td>
                      <td>{release.title}</td>
                      <td>{formatDate(release.released_at)}</td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-ghost"
                          disabled={saving}
                          title={
                            release.is_published
                              ? "Скрыть со страницы «Обновления»"
                              : "Показать на странице «Обновления»"
                          }
                          onClick={() => void togglePublished(release)}
                        >
                          {release.is_published ? "✅ виден" : "🚫 скрыт"}
                        </button>
                      </td>
                      <td className="admin-abuse-actions">
                        <button
                          type="button"
                          className="btn btn-ghost"
                          disabled={saving}
                          title="Редактировать текст, номер или дату"
                          onClick={() => startEdit(release)}
                        >
                          ✏️ Ред.
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost"
                          disabled={saving}
                          onClick={() => setConfirmDelete(release)}
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
        title="Удалить релиз?"
        confirmLabel="Удалить"
        confirmLoading={saving}
        onConfirm={() => void handleDelete()}
        onCancel={() => setConfirmDelete(null)}
      >
        {confirmDelete
          ? `Удалить релиз ${confirmDelete.version} «${confirmDelete.title}»? Он исчезнет со ` +
            "страницы «Обновления»."
          : ""}
      </ConfirmModal>
    </AdminShell>
  );
}

export function AdminReleasesPage() {
  return <RequireAdmin>{() => <AdminReleasesContent />}</RequireAdmin>;
}
