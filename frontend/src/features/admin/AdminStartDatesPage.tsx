import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "./AdminShell";
import { ConfirmModal } from "../../components/ConfirmModal";
import { PlatformBadge } from "../../components/PlatformBadge";
import { RequireAdmin } from "../../components/RequireAdmin";
import { platformCodeLabel } from "../../lib/format";
import {
  createSpecialStartDate,
  deleteSpecialStartDate,
  listSpecialStartDates,
  updateSpecialStartDate,
  type SpecialStartDate,
  type SpecialStartDateDraft,
} from "../planner/plannerApi";
import { formatWeekdayDayMonth } from "../planner/plannerDates";
import { moscowTodayIso } from "../planner/NextPlanCard";
import { AdminSubnav } from "./AdminSubnav";
import "../planner/plansPage.css";

// Системы, у которых планируются старты: parkrun не планируем (решение
// Дмитрия), поэтому и особых дат у него нет.
const PLATFORMS = ["five_verst", "s95", "runpark"] as const;

const EMPTY_DRAFT: SpecialStartDateDraft = {
  start_date: "",
  title: "",
  platforms: [...PLATFORMS],
};

function toDraft(item: SpecialStartDate): SpecialStartDateDraft {
  return { start_date: item.start_date, title: item.title, platforms: [...item.platforms] };
}

/** Что не так с формой — до запроса, чтобы не гонять сервер ради пустого поля. */
function draftProblem(draft: SpecialStartDateDraft): string | null {
  if (!draft.start_date) return "Укажите дату";
  if (!draft.title.trim()) return "Укажите название";
  if (draft.platforms.length === 0) return "Отметьте хотя бы одну систему";
  return null;
}

function AdminStartDatesContent() {
  const [items, setItems] = useState<SpecialStartDate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<SpecialStartDateDraft>(EMPTY_DRAFT);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [formSuccess, setFormSuccess] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<SpecialStartDate | null>(null);
  // Ошибка удаления — отдельно от ошибки загрузки: та прячет таблицу, а после
  // неудачного удаления список как раз нужен (дату могли убрать в другой вкладке).
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const todayIso = moscowTodayIso();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await listSpecialStartDates();
      setItems([...response.items].sort((a, b) => (a.start_date < b.start_date ? -1 : 1)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить особые даты");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const resetForm = () => {
    setEditingId(null);
    setDraft(EMPTY_DRAFT);
    setFormError(null);
    setFormSuccess(null);
  };

  const startEdit = (item: SpecialStartDate) => {
    setEditingId(item.id);
    setDraft(toDraft(item));
    setFormError(null);
    setFormSuccess(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const togglePlatform = (code: string, checked: boolean) => {
    setDraft((current) => ({
      ...current,
      // Порядок систем — как в списке, а не как кликали: так проще сверять таблицу.
      platforms: PLATFORMS.filter((item) =>
        item === code ? checked : current.platforms.includes(item),
      ),
    }));
  };

  const handleSave = async () => {
    setFormSuccess(null);
    const problem = draftProblem(draft);
    if (problem) {
      setFormError(problem);
      return;
    }
    setFormError(null);
    setSaving(true);
    const payload = { ...draft, title: draft.title.trim() };
    try {
      if (editingId) {
        await updateSpecialStartDate(editingId, payload);
        setFormSuccess("Дата обновлена");
      } else {
        await createSpecialStartDate(payload);
        setFormSuccess("Дата добавлена");
      }
      setEditingId(null);
      setDraft(EMPTY_DRAFT);
      await load();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Не удалось сохранить дату");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!confirmDelete) {
      return;
    }
    setSaving(true);
    setDeleteError(null);
    try {
      await deleteSpecialStartDate(confirmDelete.id);
      if (editingId === confirmDelete.id) {
        resetForm();
      }
      setConfirmDelete(null);
      await load();
    } catch (err) {
      setConfirmDelete(null);
      setDeleteError(err instanceof Error ? err.message : "Не удалось удалить дату");
      // Перечитываем список: при 404 даты уже нет, и таблица должна это показать.
      await load();
    } finally {
      setSaving(false);
    }
  };

  return (
    <AdminShell title="Особые даты стартов">
      <AdminSubnav activePath="/admin/start-dates" />

      <section className="card admin-abuse-form">
        <h2 className="section-title">{editingId ? "Изменить дату" : "Добавить дату"}</h2>
        <p className="muted admin-abuse-form-hint">
          Старты не в субботу — например, 1 января. На эти даты участники могут запланировать
          старт на локациях отмеченных систем. Время старта планировщик не показывает — план про
          дату. parkrun не планируется.
        </p>
        <label className="field">
          <span className="field-label">Дата</span>
          <input
            className="input"
            type="date"
            value={draft.start_date}
            onChange={(event) => setDraft({ ...draft, start_date: event.target.value })}
          />
        </label>
        <label className="field">
          <span className="field-label">Название</span>
          <input
            className="input"
            type="text"
            value={draft.title}
            maxLength={128}
            onChange={(event) => setDraft({ ...draft, title: event.target.value })}
            placeholder="Например: Новогодний старт"
          />
        </label>
        <fieldset className="field plans-admin-platforms">
          <legend className="field-label">Системы</legend>
          {PLATFORMS.map((code) => (
            <label key={code} className="login-consent-field">
              <input
                type="checkbox"
                checked={draft.platforms.includes(code)}
                onChange={(event) => togglePlatform(code, event.target.checked)}
              />
              <span>{platformCodeLabel(code)}</span>
            </label>
          ))}
        </fieldset>
        {formError && (
          <div className="profile-form-error" role="alert">
            <p>{formError}</p>
          </div>
        )}
        {formSuccess && <p className="success-text">{formSuccess}</p>}
        <div className="actions-row">
          <button type="button" className="btn primary" disabled={saving} onClick={() => void handleSave()}>
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
          <h2 className="section-title">Особые даты ({items.length})</h2>
          {deleteError && (
            <div className="profile-form-error plans-admin-delete-error" role="alert">
              <p>{deleteError}</p>
            </div>
          )}
          {items.length === 0 ? (
            <p className="muted">Особых дат пока нет — добавьте первую в форме выше.</p>
          ) : (
            <div className="table-scroll">
              <table className="data-table admin-abuse-table">
                <thead>
                  <tr>
                    <th>Дата</th>
                    <th>Название</th>
                    <th>Системы</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.id} className={item.start_date < todayIso ? "plans-admin-past" : undefined}>
                      <td>
                        {formatWeekdayDayMonth(item.start_date, true)}
                        {item.start_date < todayIso && <span className="muted"> · прошла</span>}
                      </td>
                      <td>{item.title}</td>
                      <td>
                        <span className="plans-admin-badges">
                          {item.platforms.map((code) => (
                            <PlatformBadge key={code} code={code} />
                          ))}
                        </span>
                      </td>
                      <td className="admin-abuse-actions">
                        <button
                          type="button"
                          className="btn btn-ghost"
                          disabled={saving}
                          title="Изменить дату, название или системы"
                          onClick={() => startEdit(item)}
                        >
                          ✏️ Ред.
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost"
                          disabled={saving}
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

      <ConfirmModal
        open={confirmDelete !== null}
        title="Удалить особую дату?"
        confirmLabel="Удалить"
        variant="danger"
        confirmLoading={saving}
        onConfirm={() => void handleDelete()}
        onCancel={() => setConfirmDelete(null)}
      >
        {confirmDelete
          ? `${formatWeekdayDayMonth(confirmDelete.start_date, true)} «${confirmDelete.title}» пропадёт ` +
            "из календаря локаций. Новые планы на эту дату станут недоступны."
          : ""}
      </ConfirmModal>
    </AdminShell>
  );
}

export function AdminStartDatesPage() {
  return <RequireAdmin>{() => <AdminStartDatesContent />}</RequireAdmin>;
}
