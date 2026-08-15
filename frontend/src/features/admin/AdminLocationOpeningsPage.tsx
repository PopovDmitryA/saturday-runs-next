import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "./AdminShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import {
  clearAdminLocationOpening,
  getAdminLocationOpenings,
  setAdminLocationOpening,
  type LocationOpeningEvent,
  type LocationOpeningItem,
} from "../../lib/api";
import { formatDate, pluralizeRu } from "../../lib/format";
import { AdminSubnav } from "./AdminSubnav";
import "./adminOpenings.css";

// Порядок как на портале: активные системы, затем архив parkrun. С95 первой —
// ради неё страница и заведена, остальные системы приходят сюда только чтобы
// погасить ложное открытие.
const PLATFORMS: { code: string; label: string }[] = [
  { code: "s95", label: "С95" },
  { code: "five_verst", label: "5 вёрст" },
  { code: "runpark", label: "RunPark" },
  { code: "parkrun", label: "parkrun" },
];

// Откуда взялся номер. У «none» подписи нет: в соседней ячейке и так написано
// «открытия нет», и бейдж только дублировал бы её.
const SOURCE_LABELS: Record<string, string> = {
  manual: "вручную",
  auto: "забег №1",
};

const FINISHER_FORMS = ["финишёр", "финишёра", "финишёров"] as const;

type Draft = { number: string; note: string };

function toDraft(item: LocationOpeningItem): Draft {
  return {
    number: item.opening_source === "manual" && item.opening_event_number != null
      ? String(item.opening_event_number)
      : "",
    note: item.note ?? "",
  };
}

function EventLabel({ event }: { event: LocationOpeningEvent }) {
  return (
    <>
      №{event.event_number ?? "—"} · {formatDate(event.event_date)}
      {event.finishers != null && (
        <span className="muted"> · {pluralizeRu(event.finishers, FINISHER_FORMS)}</span>
      )}
    </>
  );
}

function OpeningRow({
  item,
  onSaved,
  onError,
}: {
  item: LocationOpeningItem;
  onSaved: (saved: LocationOpeningItem) => void;
  onError: (message: string) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(item));
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(toDraft(item));
  }, [item]);

  const save = async () => {
    const trimmed = draft.number.trim();
    const parsed = trimmed === "" ? null : Number(trimmed);
    if (parsed !== null && (!Number.isInteger(parsed) || parsed < 1)) {
      onError("Номер старта — целое число от 1");
      return;
    }
    setSaving(true);
    try {
      const saved = await setAdminLocationOpening(item.location_id, {
        opening_event_number: parsed,
        note: draft.note.trim() || null,
      });
      onSaved({
        ...item,
        opening_event_number: saved.opening_event_number,
        opening_source: saved.opening_source,
        opening_event: saved.opening_event,
        opening_event_missing: saved.opening_event_missing,
        note: saved.note,
        updated_at: saved.updated_at,
        first_events: item.first_events.map((event) => ({
          ...event,
          is_opening: event.event_number === saved.opening_event_number,
        })),
      });
    } catch (err) {
      onError(err instanceof Error ? err.message : "Не удалось сохранить открытие");
    } finally {
      setSaving(false);
    }
  };

  const reset = async () => {
    setSaving(true);
    try {
      const saved = await clearAdminLocationOpening(item.location_id);
      onSaved({
        ...item,
        opening_event_number: saved.opening_event_number,
        opening_source: saved.opening_source,
        opening_event: saved.opening_event,
        opening_event_missing: saved.opening_event_missing,
        note: null,
        updated_at: null,
        first_events: item.first_events.map((event) => ({
          ...event,
          is_opening: event.event_number === saved.opening_event_number,
        })),
      });
    } catch (err) {
      onError(err instanceof Error ? err.message : "Не удалось снять разметку");
    } finally {
      setSaving(false);
    }
  };

  return (
    <tr>
      <td>
        <div className="admin-openings-name">
          {item.source_url ? (
            <a href={item.source_url} target="_blank" rel="noreferrer">
              {item.location_name}
            </a>
          ) : (
            <span>{item.location_name}</span>
          )}
          <span className="muted">{item.location_city || item.external_key}</span>
        </div>
      </td>
      <td>
        {item.opening_event ? (
          <span className="admin-openings-current">
            <EventLabel event={item.opening_event} />
          </span>
        ) : (
          <span className="muted">
            {item.opening_event_missing ? "старта с таким номером нет" : "открытия нет"}
          </span>
        )}
        {SOURCE_LABELS[item.opening_source] && (
          <span className={`admin-openings-source admin-openings-source-${item.opening_source}`}>
            {SOURCE_LABELS[item.opening_source]}
          </span>
        )}
      </td>
      <td>
        {/* Подсказка «какой старт был открытием»: по дате и числу финишёров
            торжественный первый забег обычно видно сразу. Клик подставляет
            номер в поле — набирать его руками незачем. */}
        <ul className="admin-openings-events">
          {item.first_events.map((event) => (
            <li key={event.event_id}>
              <button
                type="button"
                className={`admin-openings-event${event.is_opening ? " is-opening" : ""}`}
                onClick={() => setDraft((prev) => ({ ...prev, number: String(event.event_number ?? "") }))}
              >
                <EventLabel event={event} />
              </button>
            </li>
          ))}
          {item.first_events.length === 0 && <li className="muted">протоколов нет</li>}
        </ul>
      </td>
      <td>
        <div className="admin-openings-edit">
          <input
            className="input admin-openings-number"
            type="number"
            min={1}
            value={draft.number}
            placeholder="№"
            onChange={(event) => setDraft((prev) => ({ ...prev, number: event.target.value }))}
          />
          <input
            className="input"
            type="text"
            value={draft.note}
            placeholder="Заметка (необязательно)"
            onChange={(event) => setDraft((prev) => ({ ...prev, note: event.target.value }))}
          />
          <div className="actions-row">
            <button
              type="button"
              className="btn primary"
              disabled={saving}
              onClick={() => void save()}
            >
              {saving ? "…" : "Сохранить"}
            </button>
            {item.opening_source === "manual" && (
              <button
                type="button"
                className="btn btn-ghost"
                disabled={saving}
                onClick={() => void reset()}
              >
                Снять
              </button>
            )}
          </div>
          {item.updated_by && (
            <span className="muted admin-openings-meta">Правил: {item.updated_by}</span>
          )}
        </div>
      </td>
    </tr>
  );
}

function AdminLocationOpeningsContent() {
  const [platform, setPlatform] = useState("s95");
  const [query, setQuery] = useState("");
  const [onlyMissing, setOnlyMissing] = useState(false);
  const [items, setItems] = useState<LocationOpeningItem[]>([]);
  const [summary, setSummary] = useState({ total: 0, withOpening: 0, needsManual: false });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await getAdminLocationOpenings({
        platform,
        q: query.trim() || undefined,
        onlyMissing,
      });
      setItems(response.items);
      setSummary({
        total: response.total,
        withOpening: response.with_opening,
        needsManual: response.needs_manual,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить разметку");
    } finally {
      setLoading(false);
    }
  }, [platform, query, onlyMissing]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSaved = (saved: LocationOpeningItem) => {
    setItems((prev) =>
      prev.map((item) => (item.location_id === saved.location_id ? saved : item)),
    );
  };

  return (
    <AdminShell title="Открытия локаций">
      <AdminSubnav activePath="/admin/location-openings" />

      <section className="card">
        <h2 className="section-title">Какой старт считается открытием</h2>
        <p className="muted">
          По этой разметке считается рейтинг «Открытия локаций» (
          <a href="/ratings/openings">/ratings/openings</a>). У 5 вёрст, parkrun и RunPark
          торжественное открытие видно из протокола — это забег №1. У С95 по номерам
          забегов открытие не опознать, поэтому его номер для каждой локации С95 нужно
          проставить руками — до этого открытий у неё нет.
        </p>
        <p className="muted">
          Пустой номер у сохранённой строки — это «открытия нет»: так гасится ложное
          открытие там, где система начала вести протоколы позже самой локации. Кнопка
          «Снять» возвращает локацию к правилу её системы. Правка сразу пересчитывает
          рейтинг — кэш сбрасывается.
        </p>
      </section>

      <section className="card">
        <div className="admin-openings-filters">
          <div className="admin-openings-tabs" role="group" aria-label="Система">
            {PLATFORMS.map((option) => (
              <button
                key={option.code}
                type="button"
                aria-pressed={platform === option.code}
                className={`btn${platform === option.code ? " primary" : " btn-ghost"}`}
                onClick={() => setPlatform(option.code)}
              >
                {option.label}
              </button>
            ))}
          </div>
          <input
            className="input admin-openings-search"
            type="search"
            value={query}
            placeholder="Поиск по названию, городу или слагу"
            onChange={(event) => setQuery(event.target.value)}
          />
          <label className="admin-openings-check">
            <input
              type="checkbox"
              checked={onlyMissing}
              onChange={(event) => setOnlyMissing(event.target.checked)}
            />
            Только без открытия
          </label>
        </div>
        <p className="muted">
          Локаций: {summary.total}. С открытием: {summary.withOpening}.
          {summary.needsManual && " Остальные в рейтинг не идут, пока номер не проставлен."}
        </p>
      </section>

      {loading && <p className="muted">Загрузка…</p>}
      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!loading && !error && (
        <section className="card">
          {items.length === 0 ? (
            <p className="muted">Ничего не нашлось.</p>
          ) : (
            <div className="table-scroll">
              <table className="data-table admin-openings-table">
                <thead>
                  <tr>
                    <th>Локация</th>
                    <th>Открытие</th>
                    <th>Первые старты</th>
                    <th>Разметка</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <OpeningRow
                      key={item.location_id}
                      item={item}
                      onSaved={handleSaved}
                      onError={setError}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </AdminShell>
  );
}

export function AdminLocationOpeningsPage() {
  return (
    <RequireAdmin>
      <AdminLocationOpeningsContent />
    </RequireAdmin>
  );
}
