import { useCallback, useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { ConfirmModal } from "../../components/ConfirmModal";
import { RequireAdmin } from "../../components/RequireAdmin";
import {
  createAdminLocationContactLink,
  deleteAdminLocationContactLink,
  getAdminLocationContacts,
  updateAdminLocationAnnounceSettings,
  updateAdminLocationContactLink,
  type LocationContactItem,
  type LocationContactLink,
  type LocationContactList,
} from "../../lib/api";
import { AdminSubnav } from "./AdminSubnav";

type SettingsDraft = { do_not_disturb: boolean; comment: string };
type LinkDraft = { telegram_url: string; label: string };

function toSettingsDraft(item: LocationContactItem): SettingsDraft {
  return { do_not_disturb: item.do_not_disturb, comment: item.comment ?? "" };
}

function isSettingsDirty(item: LocationContactItem, draft: SettingsDraft): boolean {
  return draft.do_not_disturb !== item.do_not_disturb || draft.comment !== (item.comment ?? "");
}

function toLinkDraft(link: LocationContactLink): LinkDraft {
  return { telegram_url: link.telegram_url, label: link.label ?? "" };
}

function isLinkDirty(link: LocationContactLink, draft: LinkDraft): boolean {
  return draft.telegram_url !== link.telegram_url || draft.label !== (link.label ?? "");
}

function LocationLinksEditor({
  item,
  onChanged,
  onError,
}: {
  item: LocationContactItem;
  onChanged: (locationId: string, contacts: LocationContactLink[]) => void;
  onError: (message: string) => void;
}) {
  const [drafts, setDrafts] = useState<Record<string, LinkDraft>>(
    Object.fromEntries(item.contacts.map((link) => [link.id, toLinkDraft(link)])),
  );
  const [savingId, setSavingId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<LocationContactLink | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [newDraft, setNewDraft] = useState<LinkDraft>({ telegram_url: "", label: "" });

  useEffect(() => {
    setDrafts(Object.fromEntries(item.contacts.map((link) => [link.id, toLinkDraft(link)])));
  }, [item.contacts]);

  const saveLink = async (link: LocationContactLink) => {
    const draft = drafts[link.id];
    if (!draft) return;
    setSavingId(link.id);
    try {
      const updated = await updateAdminLocationContactLink(link.id, {
        telegram_url: draft.telegram_url.trim(),
        label: draft.label.trim() || null,
      });
      onChanged(
        item.location_id,
        item.contacts.map((c) => (c.id === updated.id ? updated : c)),
      );
    } catch (err) {
      onError(err instanceof Error ? err.message : "Не удалось сохранить ссылку");
    } finally {
      setSavingId(null);
    }
  };

  const confirmDeleteLink = async () => {
    if (!confirmDelete) return;
    setSavingId(confirmDelete.id);
    try {
      await deleteAdminLocationContactLink(confirmDelete.id);
      onChanged(
        item.location_id,
        item.contacts.filter((c) => c.id !== confirmDelete.id),
      );
      setConfirmDelete(null);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Не удалось удалить ссылку");
    } finally {
      setSavingId(null);
    }
  };

  const addLink = async () => {
    const url = newDraft.telegram_url.trim();
    if (!url) return;
    setSavingId("new");
    try {
      const created = await createAdminLocationContactLink(item.location_id, {
        telegram_url: url,
        label: newDraft.label.trim() || null,
      });
      onChanged(item.location_id, [...item.contacts, created]);
      setNewDraft({ telegram_url: "", label: "" });
      setAddOpen(false);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Не удалось добавить ссылку");
    } finally {
      setSavingId(null);
    }
  };

  return (
    <div className="location-links-editor">
      {item.contacts.map((link) => {
        const draft = drafts[link.id] ?? toLinkDraft(link);
        const dirty = isLinkDirty(link, draft);
        return (
          <div key={link.id} className="location-link-row">
            <input
              className="input"
              type="text"
              value={draft.telegram_url}
              placeholder="https://t.me/..."
              onChange={(event) =>
                setDrafts((prev) => ({
                  ...prev,
                  [link.id]: { ...draft, telegram_url: event.target.value },
                }))
              }
            />
            <input
              className="input location-link-label"
              type="text"
              value={draft.label}
              placeholder="Название (необяз.)"
              onChange={(event) =>
                setDrafts((prev) => ({ ...prev, [link.id]: { ...draft, label: event.target.value } }))
              }
            />
            <button
              type="button"
              className="btn secondary btn-sm"
              disabled={!dirty || savingId === link.id}
              onClick={() => void saveLink(link)}
            >
              {savingId === link.id ? "…" : "Сохранить"}
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={savingId === link.id}
              onClick={() => setConfirmDelete(link)}
            >
              Удалить
            </button>
          </div>
        );
      })}

      {addOpen ? (
        <div className="location-link-row">
          <input
            className="input"
            type="text"
            value={newDraft.telegram_url}
            placeholder="https://t.me/..."
            autoFocus
            onChange={(event) => setNewDraft((prev) => ({ ...prev, telegram_url: event.target.value }))}
          />
          <input
            className="input location-link-label"
            type="text"
            value={newDraft.label}
            placeholder="Название (необяз.)"
            onChange={(event) => setNewDraft((prev) => ({ ...prev, label: event.target.value }))}
          />
          <button
            type="button"
            className="btn primary btn-sm"
            disabled={!newDraft.telegram_url.trim() || savingId === "new"}
            onClick={() => void addLink()}
          >
            {savingId === "new" ? "…" : "Добавить"}
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => {
              setAddOpen(false);
              setNewDraft({ telegram_url: "", label: "" });
            }}
          >
            Отмена
          </button>
        </div>
      ) : (
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setAddOpen(true)}>
          + Добавить ссылку
        </button>
      )}

      <ConfirmModal
        open={confirmDelete !== null}
        title="Удалить ссылку?"
        confirmLabel="Удалить"
        confirmLoading={savingId === confirmDelete?.id}
        onConfirm={() => void confirmDeleteLink()}
        onCancel={() => setConfirmDelete(null)}
      >
        {confirmDelete ? `Убрать ссылку ${confirmDelete.telegram_url} у локации «${item.location_name}»?` : ""}
      </ConfirmModal>
    </div>
  );
}

function AdminLocationContactsContent() {
  const [data, setData] = useState<LocationContactList | null>(null);
  const [settingsDrafts, setSettingsDrafts] = useState<Record<string, SettingsDraft>>({});
  const [query, setQuery] = useState("");
  const [onlyMissing, setOnlyMissing] = useState(false);
  const [onlyDoNotDisturb, setOnlyDoNotDisturb] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [savedId, setSavedId] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    getAdminLocationContacts({ q: query, onlyMissing, onlyDoNotDisturb })
      .then((result) => {
        setData(result);
        setSettingsDrafts(
          Object.fromEntries(result.items.map((item) => [item.location_id, toSettingsDraft(item)])),
        );
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить контакты"));
  }, [query, onlyMissing, onlyDoNotDisturb]);

  useEffect(() => {
    const timer = window.setTimeout(load, 300);
    return () => window.clearTimeout(timer);
  }, [load]);

  const saveSettings = async (item: LocationContactItem) => {
    const draft = settingsDrafts[item.location_id];
    if (!draft) return;
    setSavingId(item.location_id);
    setError(null);
    try {
      const updated = await updateAdminLocationAnnounceSettings(item.location_id, {
        do_not_disturb: draft.do_not_disturb,
        comment: draft.comment.trim() || null,
      });
      setData((prev) =>
        prev
          ? {
              ...prev,
              items: prev.items.map((row) =>
                row.location_id === item.location_id
                  ? { ...row, do_not_disturb: updated.do_not_disturb, comment: updated.comment, updated_at: updated.updated_at }
                  : row,
              ),
            }
          : prev,
      );
      setSavedId(item.location_id);
      window.setTimeout(() => setSavedId(null), 1500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setSavingId(null);
    }
  };

  const patchSettingsDraft = (locationId: string, patch: Partial<SettingsDraft>) => {
    setSettingsDrafts((prev) => ({ ...prev, [locationId]: { ...prev[locationId], ...patch } }));
  };

  const handleContactsChanged = (locationId: string, contacts: LocationContactLink[]) => {
    setData((prev) =>
      prev
        ? {
            ...prev,
            items: prev.items.map((row) => (row.location_id === locationId ? { ...row, contacts } : row)),
          }
        : prev,
    );
  };

  return (
    <AppShell title="Контакты локаций" activePath="/admin">
      <AdminSubnav activePath="/admin/location-contacts" />

      <section className="card">
        <h2 className="section-title">Чаты локаций</h2>
        <p className="muted">
          Ссылки на чаты 5 вёрст, S95 и RunPark — используются в рассылке о рекордах. У локации
          может быть несколько ссылок (основной чат, резервный, чат организаторов). Флаг «не
          беспокоить» убирает локацию из рассылки целиком, комментарий виден только в админке.
          {data && (
            <>
              {" "}
              Всего: {data.total} · со ссылкой: {data.with_telegram} · не беспокоим:{" "}
              {data.do_not_disturb_total}.
            </>
          )}
        </p>
        <label className="field">
          <span className="field-label">Поиск</span>
          <input
            className="input"
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Локация или город"
          />
        </label>
        <div className="admin-abuse-form-checks">
          <label className="login-consent-field">
            <input
              type="checkbox"
              checked={onlyMissing}
              onChange={(event) => setOnlyMissing(event.target.checked)}
            />
            <span>Только без ссылки</span>
          </label>
          <label className="login-consent-field">
            <input
              type="checkbox"
              checked={onlyDoNotDisturb}
              onChange={(event) => setOnlyDoNotDisturb(event.target.checked)}
            />
            <span>Только «не беспокоить»</span>
          </label>
        </div>
        {error && (
          <div className="profile-form-error" role="alert">
            <p>{error}</p>
          </div>
        )}
      </section>

      <div className="admin-location-contacts-page">
        <section className="card">
          <div className="table-wrap">
            <table className="data-table admin-location-contacts-table">
              <thead>
                <tr>
                  <th>Локация</th>
                  <th>Ссылки на чаты</th>
                  <th>Не беспокоить</th>
                  <th>Комментарий</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data?.items.map((item) => {
                  const draft = settingsDrafts[item.location_id];
                  if (!draft) return null;
                  const dirty = isSettingsDirty(item, draft);
                  return (
                    <tr key={item.location_id}>
                      <td>
                        {item.location_name}
                        <br />
                        <span className="muted">
                          {item.city ?? "—"} · {item.platform_name}
                          {item.is_cancelled ? " · закрыта" : ""}
                          {item.is_paused ? " · пауза" : ""}
                        </span>
                      </td>
                      <td>
                        <LocationLinksEditor
                          item={item}
                          onChanged={handleContactsChanged}
                          onError={setError}
                        />
                      </td>
                      <td>
                        <input
                          type="checkbox"
                          checked={draft.do_not_disturb}
                          onChange={(event) =>
                            patchSettingsDraft(item.location_id, { do_not_disturb: event.target.checked })
                          }
                        />
                      </td>
                      <td>
                        <input
                          className="input"
                          type="text"
                          value={draft.comment}
                          placeholder="Заметка для себя"
                          onChange={(event) =>
                            patchSettingsDraft(item.location_id, { comment: event.target.value })
                          }
                        />
                      </td>
                      <td>
                        <button
                          type="button"
                          className="btn secondary btn-sm"
                          disabled={!dirty || savingId === item.location_id}
                          onClick={() => void saveSettings(item)}
                        >
                          {savedId === item.location_id
                            ? "✓"
                            : savingId === item.location_id
                              ? "…"
                              : "Сохранить"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
                {data && data.items.length === 0 && (
                  <tr>
                    <td colSpan={5} className="muted">
                      Ничего не найдено
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </AppShell>
  );
}

export function AdminLocationContactsPage() {
  return <RequireAdmin>{() => <AdminLocationContactsContent />}</RequireAdmin>;
}
