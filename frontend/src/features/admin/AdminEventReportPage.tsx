import { useEffect, useMemo, useState } from "react";
import { AdminShell } from "./AdminShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import { copyToClipboard } from "../../lib/clipboard";
import {
  getAdminEventReport,
  getAdminEventReportDates,
  getAdminEventReportLocations,
  type EventReport,
  type EventReportCountedPerson,
  type EventReportDateItem,
  type EventReportLocationItem,
  type EventReportPerson,
} from "../../lib/api";
import { AdminSubnav } from "./AdminSubnav";

function formatDateRu(value: string): string {
  const [year, month, day] = value.split("-");
  return `${day}.${month}.${year}`;
}

function formatTime(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds >= 3600) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}:${String(m).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  }
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function PersonList({ people }: { people: EventReportPerson[] }) {
  if (people.length === 0) return <span className="muted">—</span>;
  return (
    <>
      {people.map((person, index) => (
        <span key={person.participant_id ?? index}>
          {index > 0 && ", "}
          {person.name ?? "Без имени"}
        </span>
      ))}
    </>
  );
}

function StatRow({ label, people }: { label: string; people: EventReportPerson[] }) {
  return (
    <li className="admin-event-report-stat-row">
      <span className="admin-event-report-stat-label">
        {label} <strong>({people.length})</strong>:
      </span>{" "}
      <PersonList people={people} />
    </li>
  );
}

function groupByCount(items: EventReportCountedPerson[]): Array<[number, EventReportCountedPerson[]]> {
  const map = new Map<number, EventReportCountedPerson[]>();
  for (const item of items) {
    const bucket = map.get(item.count) ?? [];
    bucket.push(item);
    map.set(item.count, bucket);
  }
  return [...map.entries()].sort((a, b) => b[0] - a[0]);
}

function AdminEventReportContent() {
  const [locations, setLocations] = useState<EventReportLocationItem[] | null>(null);
  const [locationFilter, setLocationFilter] = useState("");
  const [locationId, setLocationId] = useState("");
  const [dates, setDates] = useState<EventReportDateItem[] | null>(null);
  const [eventId, setEventId] = useState("");
  const [report, setReport] = useState<EventReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    getAdminEventReportLocations()
      .then((result) => setLocations(result.items))
      .catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить локации"));
  }, []);

  const filteredLocations = useMemo(() => {
    if (!locations) return [];
    const needle = locationFilter.trim().toLowerCase();
    if (!needle) return locations;
    return locations.filter(
      (item) =>
        item.location_name.toLowerCase().includes(needle) ||
        (item.city ?? "").toLowerCase().includes(needle),
    );
  }, [locations, locationFilter]);

  useEffect(() => {
    setDates(null);
    setEventId("");
    if (!locationId) return;
    getAdminEventReportDates(locationId)
      .then((result) => {
        setDates(result.items);
        setEventId(result.items[0]?.event_id ?? "");
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить даты"));
  }, [locationId]);

  const buildReport = async () => {
    if (!eventId) return;
    setLoading(true);
    setError(null);
    setReport(null);
    setCopied(false);
    try {
      setReport(await getAdminEventReport(eventId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сформировать отчёт");
    } finally {
      setLoading(false);
    }
  };

  const copyPost = async () => {
    if (!report) return;
    const ok = await copyToClipboard(report.post_text);
    if (!ok) {
      setError("Не удалось скопировать — выделите текст поста вручную");
      return;
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  const openTelegram = async (telegramUrl: string) => {
    if (!report) return;
    await copyToClipboard(report.post_text);
    window.open(telegramUrl, "_blank", "noopener");
  };

  return (
    <AdminShell title="Отчёт по событию">
      <AdminSubnav activePath="/admin/event-report" />

      <section className="card">
        <h2 className="section-title">Отчёт по пробежке</h2>
        <p className="muted">
          Выберите локацию и дату события — получите статистику пробежки и готовый текст поста
          для Telegram-канала.
        </p>
        <label className="field">
          <span className="field-label">Поиск локации</span>
          <input
            className="input"
            type="text"
            value={locationFilter}
            onChange={(event) => setLocationFilter(event.target.value)}
            placeholder="Название локации или город"
          />
        </label>
        <label className="field">
          <span className="field-label">Локация</span>
          <select
            className="input"
            value={locationId}
            onChange={(event) => setLocationId(event.target.value)}
          >
            <option value="">— выберите локацию —</option>
            {filteredLocations.map((item) => (
              <option key={item.location_id} value={item.location_id}>
                {item.location_name}
                {item.city ? ` (${item.city})` : ""} · {item.platform_name} · {item.events_count}{" "}
                событий
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Дата события</span>
          <select
            className="input"
            value={eventId}
            onChange={(event) => setEventId(event.target.value)}
            disabled={!dates || dates.length === 0}
          >
            {!dates && <option value="">— сначала выберите локацию —</option>}
            {dates && dates.length === 0 && <option value="">событий не найдено</option>}
            {dates?.map((item) => (
              <option key={item.event_id} value={item.event_id}>
                {formatDateRu(item.event_date)}
                {item.event_number ? ` · №${item.event_number}` : ""} · {item.finishers_count}{" "}
                финишей
              </option>
            ))}
          </select>
        </label>
        <div className="actions-row">
          <button
            type="button"
            className="btn primary"
            disabled={!eventId || loading}
            onClick={() => void buildReport()}
          >
            {loading ? "Формирую…" : "Сформировать отчёт"}
          </button>
        </div>
        {error && (
          <div className="profile-form-error" role="alert">
            <p>{error}</p>
          </div>
        )}
      </section>

      {report && (
        <>
          <section className="card">
            <h2 className="section-title">Пост для Telegram</h2>
            {report.event.do_not_disturb && (
              <p className="muted">🔕 Локация помечена «не беспокоить» в контактах локаций.</p>
            )}
            <div className="actions-row">
              <button type="button" className="btn primary" onClick={() => void copyPost()}>
                {copied ? "Скопировано ✓" : "Скопировать пост"}
              </button>
              {report.event.telegram_contacts.map((contact) => (
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
            {report.event.telegram_contacts.length === 0 && (
              <p className="muted">
                Ссылки на чат этой локации нет — можно добавить на странице{" "}
                <a href="/admin/location-contacts">«Контакты локаций»</a>.
              </p>
            )}
            <pre className="admin-event-report-post">{report.post_text}</pre>
          </section>

          <section className="card">
            <h2 className="section-title">
              {report.event.canonical_name ?? report.event.location_name} ·{" "}
              {formatDateRu(report.event.event_date)}
              {report.event.event_number ? ` · забег №${report.event.event_number}` : ""}
            </h2>
            <p className="muted">
              {report.event.platform_name} · событий в истории локации:{" "}
              {report.event.events_total_at_location}
              {report.event.source_url && (
                <>
                  {" · "}
                  <a href={report.event.source_url} target="_blank" rel="noreferrer">
                    протокол
                  </a>
                </>
              )}
            </p>
            <ul className="admin-event-report-summary">
              <li>
                Финишёров: <strong>{report.header.finishers}</strong>
                {report.header.previous_event_finishers !== null && (
                  <span className="muted">
                    {" "}
                    (пред. забег {report.header.previous_event_date
                      ? formatDateRu(report.header.previous_event_date)
                      : ""}{" "}
                    — {report.header.previous_event_finishers})
                  </span>
                )}
              </li>
              <li>
                Волонтёров: <strong>{report.header.volunteers}</strong>
              </li>
              <li>
                Среднее время: <strong>{formatTime(report.header.avg_time_sec)}</strong>
              </li>
              <li>
                Лучшее время: М <strong>{formatTime(report.header.best_male_sec)}</strong> · Ж{" "}
                <strong>{formatTime(report.header.best_female_sec)}</strong>
              </li>
              {report.header.attendance_record && (
                <li>
                  🚀 Рекорд посещаемости (прежний максимум — {report.header.prior_max_finishers})
                </li>
              )}
              {report.course_records.map((record) => (
                <li key={record.gender}>
                  ⚡ Рекорд трассы среди {record.gender === "M" ? "мужчин" : "женщин"}:{" "}
                  <PersonList people={[record]} /> — {formatTime(record.time_sec)}{" "}
                  <span className="muted">(прежний — {formatTime(record.previous_sec)})</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="card">
            <h2 className="section-title">🔝 Топ финишей ({report.top_finishes.length})</h2>
            {report.top_finishes.length === 0 ? (
              <p className="muted">Никто не прошёл по критериям (топ-10/1% по полу, топ-10/3% по группе).</p>
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Место</th>
                      <th>Участник</th>
                      <th>Время</th>
                      <th>Группа</th>
                      <th>Ранг в группе</th>
                      <th>Ранг по полу</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.top_finishes.map((item, index) => (
                      <tr key={item.participant_id ?? index}>
                        <td>{item.position ?? "—"}</td>
                        <td>
                          <PersonList people={[item]} />
                        </td>
                        <td>{item.finish_time_display}</td>
                        <td>{item.age_category ?? "—"}</td>
                        <td className={item.age_qualified ? "" : "muted"}>
                          {item.age_rank} / {item.age_total}
                        </td>
                        <td className={item.gender_qualified ? "" : "muted"}>
                          {item.gender_rank} / {item.gender_total}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="card">
            <h2 className="section-title">📊 Статистика мероприятия</h2>
            <ul className="admin-event-report-stats">
              <StatRow label="Первая пробежка в системе" people={report.stats.newcomers} />
              <StatRow label="Гости локации" people={report.stats.guests} />
              <StatRow label="Личные рекорды (без новичков)" people={report.stats.personal_bests} />
              <StatRow label="Рекорды на локации" people={report.stats.location_bests} />
              <StatRow label="Вернулись после перерыва (год+)" people={report.stats.comebacks} />
              <StatRow label="Первое волонтёрство" people={report.stats.first_volunteers} />
              <StatRow
                label="Первое волонтёрство в локации"
                people={report.stats.first_volunteers_at_location}
              />
              <StatRow label="Новая волонтёрская роль" people={report.stats.new_role_volunteers} />
            </ul>
          </section>

          <section className="card">
            <h2 className="section-title">🏆 Юбилеи и клубы</h2>
            <ul className="admin-event-report-stats">
              {groupByCount(report.location_milestones.runs).map(([count, people]) => (
                <li key={`loc-run-${count}`}>
                  {count} пробежек в локации: <PersonList people={people} />
                </li>
              ))}
              {groupByCount(report.location_milestones.volunteering).map(([count, people]) => (
                <li key={`loc-vol-${count}`}>
                  {count} волонтёрств в локации: <PersonList people={people} />
                </li>
              ))}
              {report.one_step.runs.map((item, index) => (
                <li key={`step-run-${index}`}>
                  ✌ {item.count} пробежек — 1 шаг до {item.next_milestone} в локации:{" "}
                  <PersonList people={[item]} />
                </li>
              ))}
              {report.one_step.volunteering.map((item, index) => (
                <li key={`step-vol-${index}`}>
                  ✌ {item.count} волонтёрств — 1 шаг до {item.next_milestone} в локации:{" "}
                  <PersonList people={[item]} />
                </li>
              ))}
              {groupByCount(report.clubs.runs).map(([count, people]) => (
                <li key={`club-run-${count}`}>
                  🌟 Клуб {count} пробежек ({report.event.platform_name}):{" "}
                  <PersonList people={people} />
                </li>
              ))}
              {groupByCount(report.clubs.volunteering).map(([count, people]) => (
                <li key={`club-vol-${count}`}>
                  🌟 Клуб {count} волонтёрств ({report.event.platform_name}):{" "}
                  <PersonList people={people} />
                </li>
              ))}
              {groupByCount(report.global_run_jubilees).map(([count, people]) => (
                <li key={`jub-${count}`}>
                  🎖️ {count} пробежек всего: <PersonList people={people} />
                </li>
              ))}
              {report.location_milestones.runs.length === 0 &&
                report.location_milestones.volunteering.length === 0 &&
                report.one_step.runs.length === 0 &&
                report.one_step.volunteering.length === 0 &&
                report.clubs.runs.length === 0 &&
                report.clubs.volunteering.length === 0 &&
                report.global_run_jubilees.length === 0 && (
                  <li className="muted">Юбилеев на этом событии нет</li>
                )}
            </ul>
          </section>
        </>
      )}
    </AdminShell>
  );
}

export function AdminEventReportPage() {
  return <RequireAdmin>{() => <AdminEventReportContent />}</RequireAdmin>;
}
