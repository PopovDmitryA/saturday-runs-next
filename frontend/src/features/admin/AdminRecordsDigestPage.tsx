import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import { copyToClipboard } from "../../lib/clipboard";
import {
  getAdminDigestDates,
  getAdminRecordsDigest,
  type DigestCard,
  type DigestDateItem,
  type RecordsDigest,
} from "../../lib/api";
import { AdminSubnav } from "./AdminSubnav";

type Filter = "all" | "with_link" | "without_link" | "unsent" | "hidden";

type AchievementKind = "attendance" | "course" | "age" | "milestone";

const ACHIEVEMENT_OPTIONS: Array<[AchievementKind, string]> = [
  ["attendance", "Рекорд посещаемости"],
  ["course", "Рекорд трассы"],
  ["age", "Возрастной рекорд"],
  ["milestone", "Юбилеи"],
];

function cardHasAchievement(card: DigestCard, kind: AchievementKind): boolean {
  switch (kind) {
    case "attendance":
      return card.attendance_record !== null;
    case "course":
      return card.course_records.length > 0;
    case "age":
      return card.age_records.length > 0;
    case "milestone":
      return card.milestones.runs.length > 0 || card.milestones.volunteering.length > 0;
  }
}

const IMPORTANCE_LABEL: Record<DigestCard["importance"], string> = {
  high: "🔥 Крупная новость",
  medium: "⭐ Заметная новость",
  low: "Небольшая новость",
};

function formatDateRu(value: string): string {
  const [year, month, day] = value.split("-");
  return `${day}.${month}.${year}`;
}

function formatTime(seconds: number): string {
  if (seconds >= 3600) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}:${String(m).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  }
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/** Сколько простоял прежний рекорд: «3 г.», «6 нед.», «4 дн.» — «0 г.» читается плохо. */
function standingFor(previousDate: string, eventDate: string): string {
  const days = Math.max(
    0,
    Math.round((Date.parse(eventDate) - Date.parse(previousDate)) / 86_400_000),
  );
  if (days >= 365) return `${(days / 365.25).toFixed(1)} г.`;
  const weeks = Math.floor(days / 7);
  return weeks >= 1 ? `${weeks} нед.` : `${days} дн.`;
}

// Отметки «отправлено»/«скрыто» живут в браузере: это личные пометки о
// разборе рассылки на конкретную дату, хранить их в БД смысла нет — как и в
// старом HTML-отчёте. «Скрыто» — решение «этому парку по этому событию не
// пишем» (разово), в отличие от постоянного «не беспокоить» в контактах локаций.
function sentStorageKey(eventDate: string): string {
  return `digest-sent:${eventDate}`;
}

function skippedStorageKey(eventDate: string): string {
  return `digest-skipped:${eventDate}`;
}

function loadFlags(storageKey: string): Record<string, boolean> {
  try {
    return JSON.parse(window.localStorage.getItem(storageKey) ?? "{}");
  } catch {
    return {};
  }
}

function DigestCardView({
  card,
  eventDate,
  sent,
  skipped,
  onToggleSent,
  onToggleSkipped,
}: {
  card: DigestCard;
  eventDate: string;
  sent: boolean;
  skipped: boolean;
  onToggleSent: (next: boolean) => void;
  onToggleSkipped: (next: boolean) => void;
}) {
  const [copyState, setCopyState] = useState<"idle" | "done" | "failed">("idle");

  const copy = async () => {
    const ok = await copyToClipboard(card.post_text);
    setCopyState(ok ? "done" : "failed");
    window.setTimeout(() => setCopyState("idle"), 2000);
  };

  const openTelegram = async (telegramUrl: string) => {
    await copyToClipboard(card.post_text);
    window.open(telegramUrl, "_blank", "noopener");
  };

  return (
    <section
      className={`card digest-card ${sent ? "sent" : ""} ${
        card.do_not_disturb || skipped ? "muted" : ""
      }`}
    >
      <header className="digest-card-head">
        <div>
          <h3 className="digest-card-title">
            {card.location_url ? (
              <a
                href={card.location_url}
                target="_blank"
                rel="noreferrer noopener"
                className="map-popup-link"
              >
                {card.location_name}
              </a>
            ) : (
              card.location_name
            )}
            {card.city ? <span className="muted"> · {card.city}</span> : null}
            <span className="muted"> · {card.platform_name}</span>
          </h3>
          <p className="digest-card-badges">
            <span className={`digest-badge ${card.importance}`}>
              {IMPORTANCE_LABEL[card.importance]}
            </span>
            {card.do_not_disturb && <span className="digest-badge dnd">🔕 Не беспокоить</span>}
            {skipped && <span className="digest-badge dnd">🙈 Скрыто для этого события</span>}
            {card.telegram_contacts.length === 0 && !card.do_not_disturb && (
              <span className="digest-badge nolink">Нет ссылки на чат</span>
            )}
          </p>
          {card.comment && <p className="muted digest-card-comment">💬 {card.comment}</p>}
        </div>
        <div className="digest-card-actions">
          <label className="digest-sent-toggle">
            <input type="checkbox" checked={sent} onChange={(e) => onToggleSent(e.target.checked)} />
            <span>Отправлено</span>
          </label>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            title="Не отправлять по этому событию — решение только для этой даты"
            onClick={() => onToggleSkipped(!skipped)}
          >
            {skipped ? "↺ Вернуть" : "✕ Скрыть"}
          </button>
        </div>
      </header>

      <div className="digest-card-body">
        <div className="digest-facts">
          {card.course_records.map((record) => (
            <p key={`c-${record.gender}`}>
              <strong>{record.gender === "F" ? "Женский" : "Мужской"} рекорд трассы</strong>
              <br />
              {formatTime(record.record_sec)} — {record.record_name ?? "—"}
              {record.previous_sec && record.previous_date ? (
                <span className="muted">
                  {" "}
                  (прежний {formatTime(record.previous_sec)}, стоял{" "}
                  {standingFor(record.previous_date, eventDate)})
                </span>
              ) : (
                <span className="muted"> (первый рекорд)</span>
              )}
            </p>
          ))}
          {card.attendance_record && (
            <p>
              <strong>Рекорд посещаемости</strong>
              <br />
              {card.attendance_record.finishers} финишёров{" "}
              <span className="muted">(прежний {card.attendance_record.prior_max})</span>
            </p>
          )}
          {card.age_records.length > 0 && (
            <p>
              <strong>Возрастные рекорды: {card.age_records.length}</strong>
              <br />
              <span className="muted">
                {card.age_records.map((r) => r.age_group).join(", ")}
              </span>
            </p>
          )}
          {(card.milestones.runs.length > 0 || card.milestones.volunteering.length > 0) && (
            <p>
              <strong>Юбиляры</strong>
              <br />
              <span className="muted">
                {[...card.milestones.runs, ...card.milestones.volunteering]
                  .map((m) => `${m.name} (${m.count})`)
                  .join(", ")}
              </span>
            </p>
          )}
        </div>

        <div className="digest-post">
          <div className="actions-row">
            <button type="button" className="btn primary" onClick={() => void copy()}>
              {copyState === "done"
                ? "Скопировано ✓"
                : copyState === "failed"
                  ? "Не вышло — выдели вручную"
                  : "Скопировать"}
            </button>
            {card.telegram_contacts.map((contact) => (
              <button
                key={contact.id}
                type="button"
                className="btn secondary"
                onClick={() => void openTelegram(contact.telegram_url)}
              >
                {contact.label ? `Telegram — ${contact.label}` : "Telegram"}
              </button>
            ))}
          </div>
          <pre className="admin-event-report-post">{card.post_text}</pre>
        </div>
      </div>
    </section>
  );
}

function AdminRecordsDigestContent() {
  const [dates, setDates] = useState<DigestDateItem[] | null>(null);
  const [eventDate, setEventDate] = useState("");
  const [digest, setDigest] = useState<RecordsDigest | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [achievementFilters, setAchievementFilters] = useState<Set<AchievementKind>>(new Set());
  const [sent, setSent] = useState<Record<string, boolean>>({});
  const [skipped, setSkipped] = useState<Record<string, boolean>>({});

  useEffect(() => {
    getAdminDigestDates()
      .then((result) => {
        setDates(result.items);
        // По умолчанию — последний массовый день забегов. Первой в списке может
        // стоять воскресная пара стартов (2 локации), а рассылка нужна после
        // субботы, поэтому берём самую свежую дату, сопоставимую по охвату с
        // самой массовой из последних.
        const maxLocations = Math.max(...result.items.map((item) => item.locations_count), 0);
        const lastBigDay =
          result.items.find((item) => item.locations_count >= maxLocations / 2) ?? result.items[0];
        setEventDate(lastBigDay?.event_date ?? "");
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить даты"));
  }, []);

  const build = useCallback(async () => {
    if (!eventDate) return;
    setLoading(true);
    setError(null);
    setDigest(null);
    try {
      const result = await getAdminRecordsDigest(eventDate);
      setDigest(result);
      setSent(loadFlags(sentStorageKey(eventDate)));
      setSkipped(loadFlags(skippedStorageKey(eventDate)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось собрать дайджест");
    } finally {
      setLoading(false);
    }
  }, [eventDate]);

  const toggleSent = (locationId: string, next: boolean) => {
    setSent((prev) => {
      const updated = { ...prev, [locationId]: next };
      window.localStorage.setItem(sentStorageKey(eventDate), JSON.stringify(updated));
      return updated;
    });
  };

  const toggleSkipped = (locationId: string, next: boolean) => {
    setSkipped((prev) => {
      const updated = { ...prev, [locationId]: next };
      window.localStorage.setItem(skippedStorageKey(eventDate), JSON.stringify(updated));
      return updated;
    });
  };

  const toggleAchievementFilter = (kind: AchievementKind) => {
    setAchievementFilters((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  };

  const visibleCards = useMemo(() => {
    if (!digest) return [];
    return digest.cards.filter((card) => {
      if (filter === "hidden") return !!skipped[card.location_id];
      if (skipped[card.location_id]) return false;
      if (filter === "with_link" && (card.telegram_contacts.length === 0 || card.do_not_disturb)) {
        return false;
      }
      if (filter === "without_link" && (card.telegram_contacts.length > 0 || card.do_not_disturb)) {
        return false;
      }
      if (filter === "unsent" && (sent[card.location_id] || card.do_not_disturb)) return false;
      // Несколько отмеченных видов достижений — показываем локации хотя бы с
      // одним из них (наличие рекорда трассы ИЛИ рекорда посещаемости и т.д.).
      if (
        achievementFilters.size > 0 &&
        ![...achievementFilters].some((kind) => cardHasAchievement(card, kind))
      ) {
        return false;
      }
      return true;
    });
  }, [digest, filter, achievementFilters, sent, skipped]);

  const sentCount = digest
    ? digest.cards.filter((card) => sent[card.location_id]).length
    : 0;
  const skippedCount = digest
    ? digest.cards.filter((card) => skipped[card.location_id]).length
    : 0;

  return (
    <AppShell title="Рекорды локаций" activePath="/admin">
      <AdminSubnav activePath="/admin/records-digest" />

      <section className="card">
        <h2 className="section-title">Рассылка по локациям</h2>
        <p className="muted">
          Локации, где в выбранную дату есть что рассказать: рекорды трассы и возрастных групп,
          рекорд посещаемости, юбиляры. Отсортированы по важности новости.
        </p>
        <label className="field">
          <span className="field-label">Дата забегов</span>
          <select
            className="input"
            value={eventDate}
            onChange={(event) => setEventDate(event.target.value)}
          >
            {dates?.map((item) => (
              <option key={item.event_date} value={item.event_date}>
                {formatDateRu(item.event_date)} · {item.locations_count} локаций
              </option>
            ))}
          </select>
        </label>
        <div className="actions-row">
          <button
            type="button"
            className="btn primary"
            disabled={!eventDate || loading}
            onClick={() => void build()}
          >
            {loading ? "Собираю…" : "Собрать дайджест"}
          </button>
        </div>
        {loading && (
          <p className="muted">Считаю рекорды по всем локациям — обычно занимает 10–20 секунд.</p>
        )}
        {error && (
          <div className="profile-form-error" role="alert">
            <p>{error}</p>
          </div>
        )}
      </section>

      {digest && (
        <>
          <section className="card">
            <div className="digest-toolbar">
              <div className="digest-filters">
                {(
                  [
                    ["all", "Все"],
                    ["with_link", "Со ссылкой"],
                    ["without_link", "Без ссылки"],
                    ["unsent", "Неотправленные"],
                    ["hidden", `Скрытые (${skippedCount})`],
                  ] as Array<[Filter, string]>
                ).map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    className={`btn btn-sm ${filter === key ? "primary" : "btn-ghost"}`}
                    onClick={() => setFilter(key)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <p className="muted digest-stats">
                Локаций с новостями: {digest.generated_locations} · со ссылкой:{" "}
                {digest.with_telegram} · без ссылки: {digest.without_telegram} · не беспокоим:{" "}
                {digest.do_not_disturb} · отправлено: {sentCount} · скрыто: {skippedCount}
              </p>
            </div>
            <div className="digest-filters digest-achievement-filters">
              <span className="muted digest-achievement-label">Вид достижения:</span>
              {ACHIEVEMENT_OPTIONS.map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  className={`btn btn-sm ${achievementFilters.has(key) ? "primary" : "btn-ghost"}`}
                  onClick={() => toggleAchievementFilter(key)}
                >
                  {label}
                </button>
              ))}
              {achievementFilters.size > 0 && (
                <button
                  type="button"
                  className="btn btn-sm btn-ghost"
                  onClick={() => setAchievementFilters(new Set())}
                >
                  Сбросить
                </button>
              )}
            </div>
          </section>

          {visibleCards.map((card) => (
            <DigestCardView
              key={card.location_id}
              card={card}
              eventDate={digest.event_date}
              sent={!!sent[card.location_id]}
              skipped={!!skipped[card.location_id]}
              onToggleSent={(next) => toggleSent(card.location_id, next)}
              onToggleSkipped={(next) => toggleSkipped(card.location_id, next)}
            />
          ))}
          {visibleCards.length === 0 && (
            <section className="card">
              <p className="muted">Под выбранный фильтр ничего не попало.</p>
            </section>
          )}
        </>
      )}
    </AppShell>
  );
}

export function AdminRecordsDigestPage() {
  return <RequireAdmin>{() => <AdminRecordsDigestContent />}</RequireAdmin>;
}
