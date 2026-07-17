import { useCallback, useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { ConfirmModal } from "../../components/ConfirmModal";
import { PlatformBadge } from "../../components/PlatformBadge";
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

function shortTelegramUrl(url: string): string {
  return url.replace(/^https:\/\//, "");
}

/** Режим чтения: кликабельные ссылки на чаты, открываются в новой вкладке. */
function ContactLinksDisplay({ contacts }: { contacts: LocationContactLink[] }) {
  if (contacts.length === 0) {
    return <span className="muted">—</span>;
  }
  return (
    <ul className="contact-links-list">
      {contacts.map((link) => (
        <li key={link.id}>
          <a
            className="contact-link"
            href={link.telegram_url}
            target="_blank"
            rel="noreferrer"
            title={link.telegram_url}
          >
            <span className="contact-link-icon" aria-hidden="true">
              ✈️
            </span>
            {link.label ? (
              <span className="contact-link-text">
                <strong>{link.label}</strong>
                <span className="muted"> · {shortTelegramUrl(link.telegram_url)}</span>
              </span>
            ) : (
              <span className="contact-link-text">{shortTelegramUrl(link.telegram_url)}</span>
            )}
          </a>
        </li>
      ))}
    </ul>
  );
}

function LocationLinksEditor({
  item,
  onChanged,
  onError,
}: {
  item: LocationContactItem;
  onChanged: (groupKey: string, contacts: LocationContactLink[]) => void;
  onError: (message: string) => void;
}) {
  const [drafts, setDrafts] = useState<Record<string, LinkDraft>>(
    Object.fromEntries(item.contacts.map((link) => [link.id, toLinkDraft(link)])),
  );
  const [savingId, setSavingId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<LocationContactLink | null>(null);
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
        item.group_key,
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
        item.group_key,
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
      const created = await createAdminLocationContactLink(item.anchor_location_id, {
        telegram_url: url,
        label: newDraft.label.trim() || null,
      });
      onChanged(item.group_key, [...item.contacts, created]);
      setNewDraft({ telegram_url: "", label: "" });
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

      {/* Строка добавления всегда открыта: не нужен лишний клик «+ Добавить». */}
      <div className="location-link-row location-link-row-new">
        <input
          className="input"
          type="text"
          value={newDraft.telegram_url}
          placeholder="https://t.me/… — новая ссылка"
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
      </div>

      <ConfirmModal
        open={confirmDelete !== null}
        title="Удалить ссылку?"
        confirmLabel="Удалить"
        confirmLoading={savingId === confirmDelete?.id}
        onConfirm={() => void confirmDeleteLink()}
        onCancel={() => setConfirmDelete(null)}
      >
        {confirmDelete
          ? `Убрать ссылку ${confirmDelete.telegram_url} у локации «${item.display_name}»?`
          : ""}
      </ConfirmModal>
    </div>
  );
}

function AdminLocationContactsContent() {
  const [data, setData] = useState<LocationContactList | null>(null);
  const [query, setQuery] = useState("");
  const [onlyMissing, setOnlyMissing] = useState(false);
  const [onlyDoNotDisturb, setOnlyDoNotDisturb] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Правка по требованию: одна строка за раз, остальная таблица — чистый справочник.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<SettingsDraft | null>(null);
  const [savingSettings, setSavingSettings] = useState(false);

  const load = useCallback(() => {
    setError(null);
    getAdminLocationContacts({ q: query, onlyMissing, onlyDoNotDisturb })
      .then((result) => {
        setData(result);
        setEditingId(null);
        setSettingsDraft(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить контакты"));
  }, [query, onlyMissing, onlyDoNotDisturb]);

  useEffect(() => {
    const timer = window.setTimeout(load, 300);
    return () => window.clearTimeout(timer);
  }, [load]);

  const startEdit = (item: LocationContactItem) => {
    setEditingId(item.group_key);
    setSettingsDraft(toSettingsDraft(item));
  };

  const cancelEdit = () => {
    setEditingId(null);
    setSettingsDraft(null);
  };

  const finishEdit = async (item: LocationContactItem) => {
    if (!settingsDraft || !isSettingsDirty(item, settingsDraft)) {
      cancelEdit();
      return;
    }
    setSavingSettings(true);
    setError(null);
    try {
      const updated = await updateAdminLocationAnnounceSettings(item.anchor_location_id, {
        do_not_disturb: settingsDraft.do_not_disturb,
        comment: settingsDraft.comment.trim() || null,
      });
      setData((prev) =>
        prev
          ? {
              ...prev,
              items: prev.items.map((row) =>
                row.group_key === item.group_key
                  ? {
                      ...row,
                      do_not_disturb: updated.do_not_disturb,
                      comment: updated.comment,
                      updated_at: updated.updated_at,
                    }
                  : row,
              ),
            }
          : prev,
      );
      cancelEdit();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setSavingSettings(false);
    }
  };

  const handleContactsChanged = (groupKey: string, contacts: LocationContactLink[]) => {
    setData((prev) =>
      prev
        ? {
            ...prev,
            items: prev.items.map((row) =>
              row.group_key === groupKey ? { ...row, contacts } : row,
            ),
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
          Справочник чатов 5 вёрст, S95 и RunPark. Локации одной физической площадки в разных
          системах сведены в одну строку (связь берётся из справочника локаций) — чат у точки общий:
          по клику ссылка открывает чат, эти же контакты использует рассылка о рекордах на любой из
          систем площадки. Флаг «не беспокоить» убирает точку из рассылки, комментарий виден только в
          админке. Правка — по кнопке ✎ на строке.
          {data && (
            <>
              {" "}
              Точек: {data.total} · со ссылкой: {data.with_telegram} · не беспокоим:{" "}
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
                  <th className="col-location">Локация</th>
                  <th>Чаты</th>
                  <th className="col-comment">Комментарий</th>
                  <th className="col-actions" />
                </tr>
              </thead>
              <tbody>
                {data?.items.map((item) => {
                  const isEditing = editingId === item.group_key;
                  return (
                    <tr key={item.group_key} className={isEditing ? "contact-row-editing" : ""}>
                      <td>
                        <div className="contact-location-name">{item.display_name}</div>
                        <div className="muted contact-location-meta">{item.city ?? "—"}</div>
                        <div className="contact-location-platforms">
                          {item.platforms.map((platform) => (
                            <span key={platform.location_id} className="contact-platform-badge">
                              <PlatformBadge code={platform.platform_code} />
                              {(platform.is_cancelled || platform.is_paused) && (
                                <span className="muted contact-platform-flags">
                                  {platform.is_cancelled ? " закрыта" : ""}
                                  {platform.is_paused ? " пауза" : ""}
                                </span>
                              )}
                            </span>
                          ))}
                        </div>
                        {(isEditing ? settingsDraft?.do_not_disturb : item.do_not_disturb) && (
                          <span className="contact-dnd-badge">🔕 Не беспокоить</span>
                        )}
                      </td>
                      <td>
                        {isEditing ? (
                          <LocationLinksEditor
                            item={item}
                            onChanged={handleContactsChanged}
                            onError={setError}
                          />
                        ) : (
                          <ContactLinksDisplay contacts={item.contacts} />
                        )}
                      </td>
                      <td>
                        {isEditing && settingsDraft ? (
                          <div className="contact-settings-editor">
                            <textarea
                              className="input contact-comment-input"
                              rows={3}
                              value={settingsDraft.comment}
                              placeholder="Заметка для себя: с кем говорил, что за чат, нюансы"
                              onChange={(event) =>
                                setSettingsDraft((prev) =>
                                  prev ? { ...prev, comment: event.target.value } : prev,
                                )
                              }
                            />
                            <label className="login-consent-field">
                              <input
                                type="checkbox"
                                checked={settingsDraft.do_not_disturb}
                                onChange={(event) =>
                                  setSettingsDraft((prev) =>
                                    prev
                                      ? { ...prev, do_not_disturb: event.target.checked }
                                      : prev,
                                  )
                                }
                              />
                              <span>Не беспокоить анонсами</span>
                            </label>
                          </div>
                        ) : item.comment ? (
                          <div className="contact-comment">{item.comment}</div>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td className="contact-actions">
                        {isEditing ? (
                          <div className="contact-actions-editing">
                            <button
                              type="button"
                              className="btn primary btn-sm"
                              disabled={savingSettings}
                              onClick={() => void finishEdit(item)}
                            >
                              {savingSettings ? "…" : "Готово"}
                            </button>
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm"
                              disabled={savingSettings}
                              onClick={cancelEdit}
                            >
                              Отмена
                            </button>
                          </div>
                        ) : (
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            title="Изменить ссылки, комментарий и флаг «не беспокоить»"
                            onClick={() => startEdit(item)}
                          >
                            ✎ Изменить
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
                {data && data.items.length === 0 && (
                  <tr>
                    <td colSpan={4} className="muted">
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
