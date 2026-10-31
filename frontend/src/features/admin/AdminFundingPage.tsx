import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AdminShell } from "./AdminShell";
import { AdminSubnav } from "./AdminSubnav";
import { ConfirmModal } from "../../components/ConfirmModal";
import { RequireAdmin } from "../../components/RequireAdmin";
import {
  createFundingDonation,
  createFundingItem,
  deleteFundingDonation,
  formatDayMonth,
  formatRub,
  getAdminFunding,
  monthName,
  monthPrepositional,
  monthTitle,
  saveFundingCosts,
  setFundingShowPublic,
  shiftMonth,
  updateFundingDonation,
  updateFundingItem,
  type FundingAdminDonation,
  type FundingAdminMonth,
  type FundingItem,
} from "../../components/donateFundingApi";
import "./adminFunding.css";

// Донаты: переводы и расходы по месяцам для блока «Поддержать проект» на /about.
// Переводы приходят на карты и по СБП — сайт их не видит, поэтому вносятся
// здесь руками. Расходы — статьи с суммой на каждый месяц; будущий месяц —
// план, на сайте он появится, когда наступит. Правила — site_funding_service.

const UNDO_SECONDS = 10;

function todayMsk(): string {
  // sv-SE даёт ISO-вид даты: 2026-10-31.
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Moscow" }).format(new Date());
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

/** «1 500», «1500 ₽» → 1500; пусто или мусор → null. */
function parseAmount(value: string): number | null {
  const digits = value.replace(/[\s ₽]/g, "");
  if (!/^\d+$/.test(digits)) return null;
  return Number(digits);
}

function MonthStepper({
  month,
  min,
  max,
  disabled,
  onChange,
  badge,
}: {
  month: string;
  min?: string;
  max: string;
  disabled: boolean;
  onChange: (month: string) => void;
  badge?: string | null;
}) {
  const prev = shiftMonth(month, -1);
  const next = shiftMonth(month, 1);
  return (
    <div className="admin-funding-month">
      <button
        type="button"
        className="btn btn-ghost"
        disabled={disabled || (min !== undefined && prev < min)}
        onClick={() => onChange(prev)}
      >
        ‹ {monthName(prev).toLowerCase()}
      </button>
      <b>
        {monthTitle(month)}
        {badge && <span className="admin-funding-badge">{badge}</span>}
      </b>
      <button type="button" className="btn btn-ghost" disabled={disabled || next > max} onClick={() => onChange(next)}>
        {monthName(next).toLowerCase()} ›
      </button>
    </div>
  );
}

// ---------- настройки ----------

function VisibilitySection({
  showPublic,
  onChanged,
}: {
  showPublic: boolean;
  onChanged: (value: boolean) => void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = async (value: boolean) => {
    setSaving(true);
    setError(null);
    try {
      const result = await setFundingShowPublic(value);
      onChanged(result.show_public);
    } catch (err) {
      setError(errorText(err, "Не удалось сохранить"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="card admin-funding-visibility">
      <label className="login-consent-field">
        <input
          type="checkbox"
          checked={showPublic}
          disabled={saving}
          onChange={(event) => void toggle(event.target.checked)}
        />
        <span>
          Показывать на сайте: шкала месяца, расходы и кто поддержал (<a href="/about#donate">о проекте</a>,{" "}
          <a href="/backlog">бэклог</a>)
        </span>
      </label>
      <p className="muted admin-funding-hint">
        Выключено — на сайте только реквизиты, как раньше. Включается, когда у текущего месяца есть расходы.
      </p>
      {error && <p className="admin-funding-error">{error}</p>}
    </section>
  );
}

// ---------- переводы ----------

type DonationDraft = {
  amount: string;
  received_on: string;
  donor_name: string;
  is_anonymous: boolean;
};

function emptyDraft(): DonationDraft {
  return { amount: "", received_on: todayMsk(), donor_name: "", is_anonymous: false };
}

function DonationsSection({ currentMonth }: { currentMonth: string }) {
  const [month, setMonth] = useState(currentMonth);
  const [data, setData] = useState<FundingAdminMonth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [draft, setDraft] = useState<DonationDraft>(emptyDraft);
  const [editing, setEditing] = useState<FundingAdminDonation | null>(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [repeatWarned, setRepeatWarned] = useState(false);
  const [added, setAdded] = useState<FundingAdminDonation | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<FundingAdminDonation | null>(null);
  const amountRef = useRef<HTMLInputElement>(null);
  const undoTimer = useRef<number | null>(null);

  const load = useCallback(async (target: string) => {
    setLoading(true);
    setError(null);
    try {
      setData(await getAdminFunding(target));
    } catch (err) {
      setError(errorText(err, "Не удалось загрузить переводы"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(month);
  }, [load, month]);

  useEffect(
    () => () => {
      if (undoTimer.current !== null) window.clearTimeout(undoTimer.current);
    },
    [],
  );

  const showUndo = (donation: FundingAdminDonation) => {
    setAdded(donation);
    if (undoTimer.current !== null) window.clearTimeout(undoTimer.current);
    undoTimer.current = window.setTimeout(() => setAdded(null), UNDO_SECONDS * 1000);
  };

  const resetForm = () => {
    setEditing(null);
    setDraft(emptyDraft());
    setFormError(null);
    setRepeatWarned(false);
  };

  // Повтор ищем в месяце даты из формы, а не в месяце, открытом в таблице.
  const looksLikeRepeat = async (amount: number): Promise<boolean> => {
    if (editing) return false;
    const draftMonth = draft.received_on.slice(0, 7);
    const donations =
      data && data.month === draftMonth ? data.donations : (await getAdminFunding(draftMonth)).donations;
    const name = draft.donor_name.trim();
    return donations.some(
      (donation) =>
        donation.received_on === draft.received_on &&
        donation.amount_rub === amount &&
        (donation.donor_name ?? "") === name,
    );
  };

  const forgetUndo = (donationId: string) => {
    if (added?.id !== donationId) return;
    setAdded(null);
    if (undoTimer.current !== null) window.clearTimeout(undoTimer.current);
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setFormError(null);
    const amount = parseAmount(draft.amount);
    if (amount === null || amount <= 0) {
      setFormError("Введите сумму в рублях, например 500");
      return;
    }
    if (!draft.received_on) {
      setFormError("Укажите дату перевода");
      return;
    }
    const payload = {
      received_on: draft.received_on,
      amount_rub: amount,
      donor_name: draft.donor_name.trim() || null,
      is_anonymous: draft.is_anonymous,
    };
    setSaving(true);
    try {
      if (!repeatWarned && (await looksLikeRepeat(amount))) {
        setRepeatWarned(true);
        setFormError("Такой перевод в этот день уже есть. Нажмите «Добавить» ещё раз, если это не повтор.");
        return;
      }
      if (editing) {
        await updateFundingDonation(editing.id, payload);
        forgetUndo(editing.id);
        resetForm();
      } else {
        const created = await createFundingDonation(payload);
        showUndo(created);
        // Дата и галка остаются: подряд обычно вносят переводы одного дня.
        setDraft({ ...draft, amount: "", donor_name: "", is_anonymous: false });
        setRepeatWarned(false);
        amountRef.current?.focus();
      }
      const target = payload.received_on.slice(0, 7);
      if (target !== month) setMonth(target);
      else await load(month);
    } catch (err) {
      setFormError(errorText(err, "Не удалось сохранить перевод"));
    } finally {
      setSaving(false);
    }
  };

  const undoAdd = async () => {
    if (!added) return;
    const target = added;
    setAdded(null);
    try {
      await deleteFundingDonation(target.id);
      await load(month);
    } catch (err) {
      setError(errorText(err, "Не удалось отменить"));
    }
  };

  const handleDelete = async () => {
    if (!confirmDelete) return;
    setSaving(true);
    try {
      await deleteFundingDonation(confirmDelete.id);
      forgetUndo(confirmDelete.id);
      if (editing?.id === confirmDelete.id) resetForm();
      setConfirmDelete(null);
      await load(month);
    } catch (err) {
      setError(errorText(err, "Не удалось удалить перевод"));
    } finally {
      setSaving(false);
    }
  };

  const startEdit = (donation: FundingAdminDonation) => {
    setEditing(donation);
    setDraft({
      amount: String(donation.amount_rub),
      received_on: donation.received_on,
      donor_name: donation.donor_name ?? "",
      is_anonymous: donation.is_anonymous,
    });
    setFormError(null);
    setRepeatWarned(false);
    amountRef.current?.focus();
  };

  return (
    <section className="card admin-funding-section">
      <h2 className="section-title">{editing ? "Изменить перевод" : "Перевод"}</h2>
      <form className="admin-funding-form" onSubmit={(event) => void handleSubmit(event)}>
        <div className="admin-funding-row">
          <label className="field">
            <span className="field-label">Сумма, ₽</span>
            <input
              ref={amountRef}
              className="input num"
              type="text"
              inputMode="numeric"
              autoComplete="off"
              autoFocus
              value={draft.amount}
              onChange={(event) => {
                setDraft({ ...draft, amount: event.target.value });
                setRepeatWarned(false);
              }}
              placeholder="500"
            />
          </label>
          <label className="field">
            <span className="field-label">Дата</span>
            <input
              className="input"
              type="date"
              max={todayMsk()}
              value={draft.received_on}
              onChange={(event) => {
                setDraft({ ...draft, received_on: event.target.value });
                setRepeatWarned(false);
              }}
            />
          </label>
        </div>
        <label className="field">
          <span className="field-label">Кто (так будет на сайте)</span>
          <input
            className="input"
            type="text"
            list="admin-funding-donors"
            maxLength={80}
            autoComplete="off"
            value={draft.donor_name}
            onChange={(event) => {
              setDraft({ ...draft, donor_name: event.target.value });
              setRepeatWarned(false);
            }}
            placeholder="Иван П."
          />
          <datalist id="admin-funding-donors">
            {data?.donor_names.map((donor) => (
              <option key={donor.name} value={donor.name}>
                {`переводов: ${donor.count}`}
              </option>
            ))}
          </datalist>
        </label>
        <label className="login-consent-field">
          <input
            type="checkbox"
            checked={draft.is_anonymous}
            onChange={(event) => setDraft({ ...draft, is_anonymous: event.target.checked })}
          />
          <span>Анонимно — на сайте «Аноним», имя видно только здесь</span>
        </label>
        {formError && <p className="admin-funding-error">{formError}</p>}
        <div className="actions-row">
          <button type="submit" className="btn primary" disabled={saving}>
            {saving ? "Сохранение…" : editing ? "Сохранить" : "Добавить"}
          </button>
          {editing && (
            <button type="button" className="btn btn-ghost" disabled={saving} onClick={resetForm}>
              Отменить правку
            </button>
          )}
        </div>
        {added && (
          <p className="admin-funding-undo">
            Добавлено {formatRub(added.amount_rub)}
            {added.donor_name ? ` · ${added.donor_name}` : ""}
            {added.is_anonymous ? " · анонимно" : ""}
            <button type="button" className="btn btn-ghost" onClick={() => void undoAdd()}>
              Отменить
            </button>
          </p>
        )}
      </form>

      <MonthStepper
        month={month}
        max={currentMonth}
        disabled={loading}
        onChange={(target) => {
          setMonth(target);
          setAdded(null);
        }}
      />
      {error && <p className="admin-funding-error">{error}</p>}
      {loading && !data ? (
        <p className="muted">Загрузка…</p>
      ) : data && data.donations.length === 0 ? (
        <p className="muted">В этом месяце переводов нет.</p>
      ) : (
        data && (
          <div className="table-scroll">
            <table className="data-table admin-funding-table">
              <thead>
                <tr>
                  <th>Дата</th>
                  <th className="num">Сумма</th>
                  <th>Кто</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.donations.map((donation) => (
                  <tr key={donation.id} className={editing?.id === donation.id ? "is-editing" : undefined}>
                    <td className="num">{formatDayMonth(donation.received_on)}</td>
                    <td className="num">{formatRub(donation.amount_rub)}</td>
                    <td>
                      {donation.donor_name ?? <span className="muted">без имени</span>}
                      {donation.is_anonymous && <span className="admin-funding-badge muted-badge">аноним</span>}
                    </td>
                    <td className="admin-funding-actions">
                      <button
                        type="button"
                        className="btn btn-ghost"
                        disabled={saving}
                        onClick={() => startEdit(donation)}
                      >
                        ✏️ Ред.
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost"
                        disabled={saving}
                        onClick={() => setConfirmDelete(donation)}
                      >
                        Удалить
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td>Итого</td>
                  <td className="num">{formatRub(data.donated_total_rub)}</td>
                  <td colSpan={2} className="muted">
                    переводов: {data.donations.length}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        )
      )}

      <ConfirmModal
        open={confirmDelete !== null}
        title="Удалить перевод?"
        confirmLabel="Удалить"
        confirmLoading={saving}
        onConfirm={() => void handleDelete()}
        onCancel={() => setConfirmDelete(null)}
      >
        {confirmDelete
          ? `${formatDayMonth(confirmDelete.received_on)} · ${formatRub(confirmDelete.amount_rub)}` +
            (confirmDelete.donor_name ? ` · ${confirmDelete.donor_name}` : "") +
            ". Перевод пропадёт с сайта."
          : ""}
      </ConfirmModal>
    </section>
  );
}

// ---------- расходы ----------

type CostDraftLine = { item_id: string; amount: string };

function CostsSection({ currentMonth }: { currentMonth: string }) {
  const [month, setMonth] = useState(currentMonth);
  const [data, setData] = useState<FundingAdminMonth | null>(null);
  const [lines, setLines] = useState<CostDraftLine[]>([]);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [addItemId, setAddItemId] = useState("");
  const [newTitle, setNewTitle] = useState("");
  const [newAmount, setNewAmount] = useState("");
  const [editingItem, setEditingItem] = useState<FundingItem | null>(null);
  const [itemTitle, setItemTitle] = useState("");
  const [itemHint, setItemHint] = useState("");

  const applyData = (response: FundingAdminMonth) => {
    setData(response);
    setLines(response.costs.map((line) => ({ item_id: line.item_id, amount: String(line.amount_rub) })));
    setDirty(false);
  };

  const load = useCallback(async (target: string) => {
    setLoading(true);
    setError(null);
    try {
      applyData(await getAdminFunding(target));
    } catch (err) {
      setError(errorText(err, "Не удалось загрузить расходы"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(month);
  }, [load, month]);

  const itemsById = useMemo(() => new Map((data?.items ?? []).map((item) => [item.id, item])), [data]);
  const unusedItems = (data?.items ?? []).filter((item) => !lines.some((line) => line.item_id === item.id));
  const total = lines.reduce((sum, line) => sum + (parseAmount(line.amount) ?? 0), 0);
  const isPlan = month > currentMonth;

  const changeMonth = (target: string) => {
    if (dirty && !window.confirm("Изменения этого месяца не сохранены. Перейти без сохранения?")) return;
    setSuccess(null);
    setMonth(target);
  };

  const updateLine = (itemId: string, amount: string) => {
    setLines((current) => current.map((line) => (line.item_id === itemId ? { ...line, amount } : line)));
    setDirty(true);
    setSuccess(null);
  };

  const removeLine = (itemId: string) => {
    setLines((current) => current.filter((line) => line.item_id !== itemId));
    setDirty(true);
    setSuccess(null);
  };

  const addExisting = () => {
    if (!addItemId) return;
    setLines((current) => [...current, { item_id: addItemId, amount: "" }]);
    setAddItemId("");
    setDirty(true);
    setSuccess(null);
  };

  const addNew = async () => {
    const title = newTitle.trim();
    if (!title) {
      setError("Введите название статьи");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const item = await createFundingItem(title, null);
      setData((current) => (current ? { ...current, items: [...current.items, item] } : current));
      setLines((current) => [...current, { item_id: item.id, amount: newAmount }]);
      setNewTitle("");
      setNewAmount("");
      setDirty(true);
      setSuccess(null);
    } catch (err) {
      setError(errorText(err, "Не удалось добавить статью"));
    } finally {
      setSaving(false);
    }
  };

  const save = async () => {
    setError(null);
    setSuccess(null);
    const items: { item_id: string; amount_rub: number }[] = [];
    for (const line of lines) {
      const amount = parseAmount(line.amount);
      if (amount === null) {
        const title = itemsById.get(line.item_id)?.title ?? "статьи";
        setError(`Введите сумму для «${title}» (0, если в этом месяце не платили)`);
        return;
      }
      items.push({ item_id: line.item_id, amount_rub: amount });
    }
    const clearing = items.length === 0;
    if (
      clearing &&
      !window.confirm(`Убрать все расходы месяца «${monthTitle(month)}»? На сайте он перестанет открываться.`)
    ) {
      return;
    }
    setSaving(true);
    try {
      const response = await saveFundingCosts(month, items);
      applyData(response);
      if (!clearing) {
        setSuccess(`Расходы на ${monthName(month).toLowerCase()} сохранены`);
      } else if (response.costs_copied_from) {
        // Свои строки убраны, но до месяца есть заполненный — он и показывается копией.
        setSuccess(
          `Свои расходы месяца убраны. Сейчас он показывает копию месяца «${monthTitle(response.costs_copied_from)}» — ` +
            "уберите расходы и там, если учёт начинается позже.",
        );
      } else {
        setSuccess(`Расходы месяца «${monthTitle(month)}» убраны`);
      }
    } catch (err) {
      setError(errorText(err, "Не удалось сохранить расходы"));
    } finally {
      setSaving(false);
    }
  };

  // Подставляет в форму расходы прошлого месяца; в базу они попадут только по «Сохранить».
  const copyPrevious = async () => {
    const previous = shiftMonth(month, -1);
    if (dirty && !window.confirm("Изменения этого месяца не сохранены. Заменить их расходами прошлого месяца?")) return;
    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      const source = await getAdminFunding(previous);
      if (source.costs.length === 0) {
        setError(`В месяце «${monthTitle(previous)}» расходов нет`);
        return;
      }
      setData((current) => (current ? { ...current, items: source.items } : current));
      setLines(source.costs.map((line) => ({ item_id: line.item_id, amount: String(line.amount_rub) })));
      setDirty(true);
      setSuccess(
        `Подставлены расходы месяца «${monthTitle(previous)}». Поправьте, что изменилось, и нажмите «Сохранить».`,
      );
    } catch (err) {
      setError(errorText(err, "Не удалось загрузить прошлый месяц"));
    } finally {
      setLoading(false);
    }
  };

  const startEditItem = (item: FundingItem) => {
    setEditingItem(item);
    setItemTitle(item.title);
    setItemHint(item.hint ?? "");
  };

  const saveItem = async () => {
    if (!editingItem) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await updateFundingItem(editingItem.id, itemTitle, itemHint.trim() || null);
      setData((current) =>
        current
          ? { ...current, items: current.items.map((item) => (item.id === updated.id ? updated : item)) }
          : current,
      );
      setEditingItem(null);
    } catch (err) {
      setError(errorText(err, "Не удалось сохранить статью"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="card admin-funding-section">
      <h2 className="section-title">Расходы</h2>
      <MonthStepper
        month={month}
        max={data?.max_month ?? shiftMonth(currentMonth, 12)}
        disabled={loading || saving}
        onChange={changeMonth}
        badge={isPlan ? "план" : null}
      />
      {data && (
        <div className="actions-row admin-funding-copy">
          <button type="button" className="btn btn-ghost" disabled={loading || saving} onClick={() => void copyPrevious()}>
            Как в {monthPrepositional(shiftMonth(month, -1))}
          </button>
        </div>
      )}
      {data?.costs_copied_from && !dirty && (
        <p className="muted admin-funding-hint">
          Своих сумм у этого месяца нет — показана копия месяца «{monthTitle(data.costs_copied_from)}». Поправьте,
          что изменилось, и сохраните.
        </p>
      )}
      {data && !data.costs_copied_from && data.costs.length === 0 && !dirty && (
        <p className="muted admin-funding-hint">Расходов ещё нет — добавьте статьи ниже.</p>
      )}
      {isPlan && (
        <p className="muted admin-funding-hint">План: на сайте месяц появится, когда наступит.</p>
      )}
      {loading && !data && <p className="muted">Загрузка…</p>}

      {data && (
        <div className="admin-funding-costs">
          {lines.map((line) => {
            const item = itemsById.get(line.item_id);
            if (!item) return null;
            const isEditing = editingItem?.id === item.id;
            return (
              <div className="admin-funding-cost" key={line.item_id}>
                {isEditing ? (
                  <div className="admin-funding-item-edit">
                    <input
                      className="input"
                      type="text"
                      maxLength={120}
                      value={itemTitle}
                      onChange={(event) => setItemTitle(event.target.value)}
                      aria-label="Название статьи"
                    />
                    <input
                      className="input"
                      type="text"
                      maxLength={400}
                      value={itemHint}
                      onChange={(event) => setItemHint(event.target.value)}
                      placeholder="Подсказка ⓘ на сайте (необязательно)"
                      aria-label="Подсказка"
                    />
                    <div className="actions-row">
                      <button type="button" className="btn primary" disabled={saving} onClick={() => void saveItem()}>
                        Сохранить статью
                      </button>
                      <button type="button" className="btn btn-ghost" onClick={() => setEditingItem(null)}>
                        Отмена
                      </button>
                    </div>
                    <p className="muted admin-funding-hint">Название и подсказка меняются во всех месяцах.</p>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="admin-funding-cost-title"
                    title="Изменить название или подсказку"
                    onClick={() => startEditItem(item)}
                  >
                    {item.title}
                    {item.hint && <span className="muted"> ⓘ</span>}
                  </button>
                )}
                <input
                  className="input num admin-funding-amount"
                  type="text"
                  inputMode="numeric"
                  value={line.amount}
                  onChange={(event) => updateLine(line.item_id, event.target.value)}
                  aria-label={`Сумма: ${item.title}`}
                  placeholder="₽"
                />
                <button
                  type="button"
                  className="btn btn-ghost admin-funding-remove"
                  title="Убрать из этого месяца"
                  aria-label={`Убрать «${item.title}» из этого месяца`}
                  onClick={() => removeLine(line.item_id)}
                >
                  ×
                </button>
              </div>
            );
          })}

          <div className="admin-funding-total num">
            <span>Итого за {monthName(month).toLowerCase()}</span>
            <b>{formatRub(total)}</b>
          </div>

          <div className="admin-funding-add">
            <span className="field-label">Добавить статью</span>
            {unusedItems.length > 0 && (
              <div className="admin-funding-row">
                <select
                  className="input"
                  value={addItemId}
                  onChange={(event) => setAddItemId(event.target.value)}
                  aria-label="Статья из списка"
                >
                  <option value="">Из прежних статей…</option>
                  {unusedItems.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.title}
                    </option>
                  ))}
                </select>
                <button type="button" className="btn btn-ghost" disabled={!addItemId} onClick={addExisting}>
                  + В месяц
                </button>
              </div>
            )}
            <div className="admin-funding-row">
              <input
                className="input"
                type="text"
                maxLength={120}
                value={newTitle}
                onChange={(event) => setNewTitle(event.target.value)}
                placeholder="Новая статья"
                aria-label="Название новой статьи"
              />
              <input
                className="input num admin-funding-amount"
                type="text"
                inputMode="numeric"
                value={newAmount}
                onChange={(event) => setNewAmount(event.target.value)}
                placeholder="₽"
                aria-label="Сумма новой статьи"
              />
              <button type="button" className="btn btn-ghost" disabled={saving} onClick={() => void addNew()}>
                + Добавить
              </button>
            </div>
          </div>

          {error && <p className="admin-funding-error">{error}</p>}
          {success && <p className="success-text">{success}</p>}
          <div className="actions-row">
            <button type="button" className="btn primary" disabled={saving || (lines.length === 0 && !dirty)} onClick={() => void save()}>
              {saving ? "Сохранение…" : `Сохранить расходы: ${monthName(month).toLowerCase()}`}
            </button>
            {dirty && (
              <button type="button" className="btn btn-ghost" disabled={saving} onClick={() => void load(month)}>
                Сбросить изменения
              </button>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function AdminFundingContent() {
  const [overview, setOverview] = useState<FundingAdminMonth | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getAdminFunding(todayMsk().slice(0, 7))
      .then(setOverview)
      .catch((err: unknown) => setError(errorText(err, "Не удалось загрузить донаты")));
  }, []);

  return (
    <AdminShell title="Донаты">
      <AdminSubnav activePath="/admin/donations" />
      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}
      {!overview && !error && <p className="muted">Загрузка…</p>}
      {overview && (
        <>
          <VisibilitySection
            showPublic={overview.show_public}
            onChanged={(value) => setOverview({ ...overview, show_public: value })}
          />
          <div className="admin-funding-grid">
            <DonationsSection currentMonth={overview.current_month} />
            <CostsSection currentMonth={overview.current_month} />
          </div>
        </>
      )}
    </AdminShell>
  );
}

export function AdminFundingPage() {
  return <RequireAdmin>{() => <AdminFundingContent />}</RequireAdmin>;
}
