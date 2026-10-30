import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { AdminShell } from "./AdminShell";
import { AdminSubnav } from "./AdminSubnav";
import { DailyBarChart, StatCard } from "./adminStatsWidgets";
import { RequireAdmin } from "../../components/RequireAdmin";
import {
  getAdminPlans,
  getAdminPlansDay,
  getAdminPlansFeed,
  type AdminPlansDayPerson,
  type AdminPlansDayResponse,
  type AdminPlansFeedItem,
  type AdminPlansFeedResponse,
  type AdminPlansOverviewResponse,
} from "../../lib/api";
import {
  COUNT_FORMS,
  formatChartDay,
  formatDateLong,
  formatDateTime,
  formatInt,
  formatWeekdayShort,
  pluralizeRu,
} from "../../lib/format";
import "./adminPlans.css";

// Админка «Планы» (§10c AGENTS.md): все планы всех людей, как их ставят и
// сколько людей пользуется планировщиком. Админ видит и приватные планы — у
// них пометка в ленте.

const PERIODS = [7, 30, 90] as const;
const FEED_LIMIT = 50;
const SERIES_ICON = "↻";
const PEOPLE_FORMS = ["человек", "человека", "человек"] as const;
const DAY_HASH = /^#day=(\d{4}-\d{2}-\d{2})$/;
const FEED_ID = "admin-plans-feed";

/** Дата из якоря #day=YYYY-MM-DD — ссылку на «План на дату» можно отправить. */
function dayFromHash(): string | null {
  return DAY_HASH.exec(window.location.hash)?.[1] ?? null;
}

/** «Сб 31 октября», год — только если не текущий. */
function longDay(value: string): string {
  const weekday = formatWeekdayShort(value);
  const full = formatDateLong(value);
  const year = String(new Date().getFullYear());
  const text = full.endsWith(` ${year}`) ? full.slice(0, -year.length - 1) : full;
  return weekday ? `${weekday[0].toUpperCase()}${weekday.slice(1)} ${text}` : text;
}

const DELIVERY_LABELS: Record<string, string> = {
  plan_reminders: "Напоминания накануне",
  plan_cancellations: "«Старт отменён»",
  plan_series: "Постоянный план на паузе",
};

function pct(part: number, total: number): string {
  return total > 0 ? `${Math.round((part / total) * 100)}%` : "—";
}

/** Когда поставил: «30.10 11:56» — год в ленте последних недель лишний. */
function shortMoment(value: string | null): string {
  if (!value) return "—";
  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return formatDateTime(value);
  const pad = (part: number) => String(part).padStart(2, "0");
  const day = `${pad(moment.getDate())}.${pad(moment.getMonth() + 1)}`;
  const year = moment.getFullYear() === new Date().getFullYear() ? "" : `.${moment.getFullYear()}`;
  return `${day}${year} ${pad(moment.getHours())}:${pad(moment.getMinutes())}`;
}

function shortDate(value: string): string {
  return `${formatWeekdayShort(value)} ${formatChartDay(value)}`;
}

type Part = { label: string; value: number; tone: string };

/** Полоса из долей с подписями под ней: план → факт, разовые/постоянные. */
function SplitBar({ parts, ariaLabel }: { parts: Part[]; ariaLabel: string }) {
  const total = parts.reduce((sum, part) => sum + part.value, 0);
  return (
    <div className="admin-plans-split">
      <div className="admin-plans-split-bar" role="img" aria-label={ariaLabel}>
        {total > 0 &&
          parts
            .filter((part) => part.value > 0)
            .map((part) => (
              <span
                key={part.label}
                className={`admin-plans-tone-${part.tone}`}
                style={{ flexGrow: part.value }}
                title={`${part.label}: ${part.value}`}
              />
            ))}
      </div>
      <ul className="admin-plans-legend">
        {parts.map((part) => (
          <li key={part.label}>
            <i className={`admin-plans-dot admin-plans-tone-${part.tone}`} />
            {part.label} <b className="num">{formatInt(part.value)}</b>
            <span className="muted"> · {pct(part.value, total)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Горизонтальные столбики: подпись слева, число справа. */
/** Горизонтальные столбики: подпись слева, число справа. С onSelect строка —
 * кнопка (даты «Кто бежит на ближайших стартах» открывают «План на дату»). */
function HBars({
  rows,
  onSelect,
  selectedKey,
}: {
  rows: { key: string; label: string; hint?: string; values: { value: number; tone: string }[]; total: string }[];
  onSelect?: (key: string) => void;
  selectedKey?: string | null;
}) {
  const peak = Math.max(1, ...rows.map((row) => row.values.reduce((sum, item) => sum + item.value, 0)));
  return (
    <ul className={onSelect ? "admin-plans-hbars admin-plans-hbars-select" : "admin-plans-hbars"}>
      {rows.map((row) => {
        const inner = (
          <>
            <span className="admin-plans-hbar-label">
              {row.label}
              {row.hint && <small>{row.hint}</small>}
            </span>
            <span className="admin-plans-hbar-track">
              {row.values.map((item, index) =>
                item.value > 0 ? (
                  <span
                    key={index}
                    className={`admin-plans-tone-${item.tone}`}
                    style={{ width: `${(item.value / peak) * 100}%` }}
                  />
                ) : null,
              )}
            </span>
            <b className="admin-plans-hbar-value num">{row.total}</b>
          </>
        );
        return (
          <li key={row.key}>
            {onSelect ? (
              <button
                type="button"
                className="admin-plans-hbar-row"
                aria-pressed={selectedKey === row.key}
                onClick={() => onSelect(row.key)}
              >
                {inner}
              </button>
            ) : (
              <div className="admin-plans-hbar-row">{inner}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Имя человека: в публичный профиль, не в /admin/users (решение Дмитрия). */
function PersonName({ person }: { person: AdminPlansDayPerson }) {
  return person.serial_id ? <a href={`/users/${person.serial_id}`}>{person.label}</a> : <>{person.label}</>;
}

function DayPanel({
  isoDate,
  onSelect,
  onClose,
  onShowFeed,
}: {
  isoDate: string;
  onSelect: (isoDate: string) => void;
  onClose: () => void;
  onShowFeed: (isoDate: string) => void;
}) {
  const [data, setData] = useState<AdminPlansDayResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const panel = useRef<HTMLElement>(null);

  useEffect(() => {
    const current = ++seq.current;
    setError(null);
    getAdminPlansDay(isoDate)
      .then((payload) => {
        if (current === seq.current) setData(payload);
      })
      .catch((err: unknown) => {
        if (current === seq.current) setError(err instanceof Error ? err.message : "Не удалось загрузить дату");
      });
  }, [isoDate]);

  useEffect(() => {
    panel.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [isoDate]);

  const shown = data && data.date === isoDate ? data : null;
  const summary = shown?.summary;
  return (
    <section ref={panel} className="card admin-plans-card admin-plans-day" aria-labelledby="admin-plans-day-title">
      <div className="admin-plans-day-head">
        <button
          type="button"
          className="btn btn-sm secondary"
          aria-label="Предыдущий старт"
          disabled={!shown}
          onClick={() => shown && onSelect(shown.prev_date)}
        >
          ‹
        </button>
        <div className="admin-plans-day-title">
          <h2 className="section-title" id="admin-plans-day-title">
            {longDay(isoDate)}
            {summary && (
              <span className="admin-plans-day-totals">
                {" · "}
                {pluralizeRu(summary.people, PEOPLE_FORMS)}
                {" · "}
                {pluralizeRu(summary.locations, COUNT_FORMS.locations)}
              </span>
            )}
          </h2>
          <p className="muted admin-plans-day-sub">
            {shown?.special_title && <span className="admin-plans-badge admin-plans-day-special">★ {shown.special_title}</span>}
            {summary
              ? `разовых ${summary.one_off} · ${SERIES_ICON} постоянных ${summary.regular} · 🔒 приватно ${summary.private}${
                  shown?.is_past ? " · дата прошла" : ""
                }`
              : "План на дату"}
          </p>
        </div>
        <button
          type="button"
          className="btn btn-sm secondary admin-plans-day-next"
          aria-label="Следующий старт"
          disabled={!shown}
          onClick={() => shown && onSelect(shown.next_date)}
        >
          ›
        </button>
        <button type="button" className="btn btn-sm secondary admin-plans-day-close" aria-label="Закрыть" onClick={onClose}>
          ✕
        </button>
      </div>

      {error && <p className="error-text">{error}</p>}
      {!shown && !error && <p className="muted">Загрузка…</p>}
      {shown && (
        <>
          {shown.locations.length === 0 ? (
            <p className="muted">На эту дату никто не запланирован.</p>
          ) : (
            <ul className="admin-plans-day-locations">
              {shown.locations.map((location) => (
                <li key={location.key} className="admin-plans-day-location">
                  <div className="admin-plans-day-location-head">
                    {location.slug ? <a href={`/locations/${location.slug}`}>{location.name}</a> : location.name}
                    <b className="num">{formatInt(location.people.length)}</b>
                  </div>
                  <ul className="admin-plans-day-people">
                    {location.people.map((person) => (
                      <li key={`${person.serial_id ?? person.label}`}>
                        <PersonName person={person} />
                        {person.runs != null && (
                          <span className="muted num" title="Всего пробежек">
                            {" "}
                            {formatInt(person.runs)}
                          </span>
                        )}
                        {person.regular && (
                          <span className="admin-plans-day-mark admin-plans-day-regular" title="Постоянный план">
                            {SERIES_ICON}
                          </span>
                        )}
                        {!person.is_public && (
                          <span className="admin-plans-day-mark" title="Приватный план">
                            🔒
                          </span>
                        )}
                        {person.profile_private && <small className="muted"> · профиль закрыт</small>}
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}
          <div className="admin-plans-day-foot">
            <p className="admin-plans-day-rest">
              <b>Не бегают: {formatInt(shown.rest.length)}</b>
              {shown.rest.length > 0 && " — "}
              {shown.rest.map((person, index) => (
                <span key={`${person.serial_id ?? person.label}`}>
                  {index > 0 && ", "}
                  <PersonName person={person} />
                  {person.profile_private && <small className="muted"> (профиль закрыт)</small>}
                </span>
              ))}
            </p>
            <a
              href={`#${FEED_ID}`}
              onClick={(event) => {
                event.preventDefault();
                onShowFeed(shown.date);
              }}
            >
              Все записи этой даты в ленте ↓
            </a>
          </div>
        </>
      )}
    </section>
  );
}

function DayPicker({ value, onPick }: { value: string | null; onPick: (isoDate: string) => void }) {
  const [input, setInput] = useState(value ?? "");
  useEffect(() => {
    if (value) setInput(value);
  }, [value]);
  return (
    <form
      className="admin-plans-day-pick"
      onSubmit={(event) => {
        event.preventDefault();
        if (input) onPick(input);
      }}
    >
      <input
        className="input"
        type="date"
        value={input}
        aria-label="Дата старта"
        onChange={(event) => setInput(event.target.value)}
      />
      <button type="submit" className="btn btn-sm" disabled={!input}>
        Открыть
      </button>
    </form>
  );
}

function Overview({
  data,
  day,
  onSelectDay,
  onCloseDay,
  onShowFeed,
}: {
  data: AdminPlansOverviewResponse;
  day: string | null;
  onSelectDay: (isoDate: string) => void;
  onCloseDay: () => void;
  onShowFeed: (isoDate: string) => void;
}) {
  const { summary, facts, mix } = data;
  const oneOffTotal = summary.plans_future + summary.plans_past;
  return (
    <>
      <section className="admin-stats-grid">
        <StatCard label="Людей с планами" value={summary.people_total} hint={`с планами впереди — ${summary.people_future}`} />
        <StatCard label="Разовых планов впереди" value={summary.plans_future} hint={`прошедших — ${summary.plans_past}`} />
        <StatCard
          label="Публичных планов"
          value={pct(summary.plans_public, oneOffTotal)}
          hint={`${summary.plans_public} из ${oneOffTotal} разовых`}
        />
        <StatCard
          label={`${SERIES_ICON} Постоянных действуют`}
          value={summary.series_active}
          hint={`на паузе — ${summary.series_sleeping} · остановлено — ${summary.series_stopped}`}
        />
        <StatCard
          label="Отметок «Не бегаю»"
          value={summary.rest_total}
          hint={`впереди — ${summary.rest_future} · людей — ${summary.rest_people}`}
        />
      </section>

      <section className="card admin-plans-card">
        <h2 className="section-title">План → факт</h2>
        <p className="muted admin-plans-lead">
          Прошедшие планы, включая субботы постоянных: {formatInt(facts.total)}. Сверка с протоколами по правилам
          «Планов» — волонтёрство тоже «был на старте».
        </p>
        <SplitBar
          ariaLabel="План → факт"
          parts={[
            { label: "Пробежали", value: facts.ran, tone: "ran" },
            { label: "Волонтёрили", value: facts.volunteered, tone: "vol" },
            { label: "Не пришли", value: facts.missed, tone: "missed" },
            { label: "Протокола нет", value: facts.unknown, tone: "unknown" },
          ]}
        />
      </section>

      <div className="admin-stats-charts">
        <DailyBarChart
          title="Новые планы по дням"
          data={data.daily.map((day) => ({ date: day.date, value: day.plans + day.series }))}
          ariaLabel="Новые разовые и постоянные планы по дням создания"
          barClassName="analytics-chart-bar-vol"
          chartKey={`plans-${data.period_days}`}
        />
        <DailyBarChart
          title="Новые люди — первый план"
          data={data.daily.map((day) => ({ date: day.date, value: day.people }))}
          ariaLabel="Люди, впервые поставившие план, по дням"
          chartKey={`people-${data.period_days}`}
        />
      </div>

      <section className="card admin-plans-card">
        <div className="admin-plans-card-head">
          <h2 className="section-title">Кто бежит на ближайших стартах</h2>
          <ul className="admin-plans-legend admin-plans-legend-inline">
            <li>
              <i className="admin-plans-dot admin-plans-tone-one-off" />
              разовые
            </li>
            <li>
              <i className="admin-plans-dot admin-plans-tone-regular" />
              {SERIES_ICON} постоянные
            </li>
          </ul>
        </div>
        <div className="admin-plans-day-lead">
          <p className="muted admin-plans-lead">
            Люди на дату — с субботами постоянных планов, минус «Не бегаю» и паузы. Нажмите на дату — кто и куда
            запланирован.
          </p>
          <DayPicker value={day} onPick={onSelectDay} />
        </div>
        <HBars
          onSelect={onSelectDay}
          selectedKey={day}
          rows={data.load.map((day) => ({
            key: day.date,
            label: day.special_title ? `★ ${shortDate(day.date)}` : shortDate(day.date),
            hint: day.special_title ?? (day.locations > 0 ? `${day.locations} лок.` : undefined),
            values: [
              { value: day.one_off, tone: "one-off" },
              { value: day.regular, tone: "regular" },
            ],
            total: formatInt(day.people),
          }))}
        />
      </section>

      {day && <DayPanel isoDate={day} onSelect={onSelectDay} onClose={onCloseDay} onShowFeed={onShowFeed} />}

      <div className="admin-plans-two">
        <section className="card admin-plans-card">
          <h2 className="section-title">За сколько дней до старта</h2>
          <p className="muted admin-plans-lead">
            Разовые планы{data.lead_median_days != null ? ` · медиана — ${data.lead_median_days} дн.` : ""}
          </p>
          <HBars
            rows={data.lead_times.map((bucket) => ({
              key: bucket.label,
              label: bucket.label,
              values: [{ value: bucket.count, tone: "one-off" }],
              total: formatInt(bucket.count),
            }))}
          />
        </section>

        <section className="card admin-plans-card">
          <h2 className="section-title">Как пользуются</h2>
          <h3 className="admin-plans-subtitle">Люди</h3>
          <SplitBar
            ariaLabel="Разовые и постоянные планы у людей"
            parts={[
              { label: "только разовые", value: mix.only_one_off, tone: "one-off" },
              { label: `${SERIES_ICON} только постоянный`, value: mix.only_regular, tone: "regular" },
              { label: "и то и другое", value: mix.both, tone: "both" },
            ]}
          />
          <h3 className="admin-plans-subtitle">Разовые планы</h3>
          <SplitBar
            ariaLabel="Публичность разовых планов"
            parts={[
              { label: "публично", value: mix.public_plans, tone: "public" },
              { label: "приватно", value: mix.private_plans, tone: "private" },
            ]}
          />
          <h3 className="admin-plans-subtitle">{SERIES_ICON} Постоянные</h3>
          <SplitBar
            ariaLabel="Публичность постоянных планов"
            parts={[
              { label: "публично", value: mix.public_series, tone: "public" },
              { label: "приватно", value: mix.private_series, tone: "private" },
            ]}
          />
        </section>
      </div>

      <section className="card admin-plans-card">
        <h2 className="section-title">Локации</h2>
        <div className="table-scroll">
          <table className="data-table admin-plans-table">
            <thead>
              <tr>
                <th>Локация</th>
                <th className="num">Разовых</th>
                <th className="num">{SERIES_ICON}</th>
                <th className="num">Людей</th>
                <th className="num">8 недель</th>
              </tr>
            </thead>
            <tbody>
              {data.top_locations.map((location) => (
                <tr key={location.key}>
                  <td>
                    {location.slug ? <a href={`/locations/${location.slug}`}>{location.name}</a> : location.name}
                    {location.upcoming && <span className="admin-plans-badge">скоро открытие</span>}
                  </td>
                  <td className="num">{formatInt(location.plans)}</td>
                  <td className="num">{formatInt(location.series)}</td>
                  <td className="num">{formatInt(location.people)}</td>
                  <td className="num">{formatInt(location.next_weeks)}</td>
                </tr>
              ))}
              {data.top_locations.length === 0 && (
                <tr>
                  <td colSpan={5} className="muted">
                    Планов пока нет.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="muted admin-plans-foot">
          «8 недель» — люди на стартах впереди, с постоянными. Всего локаций с планами: {data.locations.length}.
          «Скоро открытие» с планами: {data.upcoming_locations.length}
          {data.upcoming_locations.length > 0 && (
            <>
              {" — "}
              {data.upcoming_locations.map((location, index) => (
                <span key={location.key}>
                  {index > 0 && ", "}
                  {location.slug ? <a href={`/locations/${location.slug}`}>{location.name}</a> : location.name} (
                  {location.count})
                </span>
              ))}
            </>
          )}
          .
        </p>
      </section>

      <section className="card admin-plans-card">
        <h2 className="section-title">Уведомления о планах</h2>
        <div className="table-scroll">
          <table className="data-table admin-plans-table">
            <thead>
              <tr>
                <th>Вид</th>
                <th className="num">Доставлено</th>
                <th className="num">Не доставлено</th>
                <th className="num">Прочие</th>
                <th className="num">За {data.period_days} дн.</th>
                <th aria-label="Журнал" />
              </tr>
            </thead>
            <tbody>
              {data.deliveries.map((item) => {
                const sent = item.by_status.sent ?? 0;
                const failed = item.by_status.failed ?? 0;
                return (
                  <tr key={item.kind}>
                    <td>{DELIVERY_LABELS[item.kind] ?? item.kind}</td>
                    <td className="num">{formatInt(sent)}</td>
                    <td className={failed > 0 ? "num admin-plans-failed" : "num"}>{formatInt(failed)}</td>
                    <td className="num">{formatInt(item.total - sent - failed)}</td>
                    <td className="num">{formatInt(item.period)}</td>
                    <td>
                      <a href={`/admin/notifications?kind=${item.kind}&period=${Math.min(data.period_days, 30)}`}>журнал →</a>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

function feedWhat(item: AdminPlansFeedItem): { text: string; tone: string } {
  switch (item.kind) {
    case "rest":
      return { text: "Не бегаю", tone: "rest" };
    case "series":
      if (item.series_status === "stopped") return { text: `${SERIES_ICON} Постоянный · остановлен`, tone: "regular-off" };
      if (item.series_status === "sleeping") return { text: `${SERIES_ICON} Постоянный · на паузе`, tone: "regular-off" };
      return { text: `${SERIES_ICON} Постоянный`, tone: "regular" };
    case "series_stop":
      return { text: `${SERIES_ICON} Остановил постоянный`, tone: "regular-off" };
    case "series_sleep":
      return { text: `${SERIES_ICON} Пауза: нет в протоколах`, tone: "regular-off" };
    default:
      return { text: "Разовый", tone: "one-off" };
  }
}

function feedDate(item: AdminPlansFeedItem): string {
  if (!item.on_date) return "—";
  if (item.kind === "series") return `с ${shortDate(item.on_date)}${item.until ? ` до ${shortDate(item.until)}` : ""}`;
  if (item.kind === "series_sleep") return `с ${shortDate(item.on_date)}`;
  if (item.kind === "series_stop") return "—";
  return shortDate(item.on_date);
}

function Feed({
  locations,
  planDate,
  onClearPlanDate,
}: {
  locations: AdminPlansOverviewResponse["locations"];
  planDate: string | null;
  onClearPlanDate: () => void;
}) {
  const [futureOnly, setFutureOnly] = useState(false);
  const [location, setLocation] = useState("");
  const [personInput, setPersonInput] = useState("");
  const [person, setPerson] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<AdminPlansFeedResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  useEffect(() => {
    setOffset(0);
  }, [planDate]);

  useEffect(() => {
    const current = ++seq.current;
    setError(null);
    getAdminPlansFeed({
      futureOnly,
      location: location || null,
      person: person || null,
      planDate,
      limit: FEED_LIMIT,
      offset,
    })
      .then((payload) => {
        if (current === seq.current) setData(payload);
      })
      .catch((err: unknown) => {
        if (current === seq.current) setError(err instanceof Error ? err.message : "Не удалось загрузить ленту");
      });
  }, [futureOnly, location, person, planDate, offset]);

  const submitPerson = (event: FormEvent) => {
    event.preventDefault();
    setPerson(personInput.trim());
    setOffset(0);
  };

  const pages = data ? Math.max(1, Math.ceil(data.total / FEED_LIMIT)) : 1;
  const page = Math.floor(offset / FEED_LIMIT) + 1;

  return (
    <section className="card admin-plans-card" id={FEED_ID}>
      <div className="admin-plans-card-head">
        <h2 className="section-title">Все планы — как ставят</h2>
        {data && <span className="muted">{formatInt(data.total)} записей</span>}
      </div>
      <div className="admin-plans-feed-toolbar">
        {planDate && (
          <span className="admin-plans-chip">
            Дата старта: {shortDate(planDate)}
            <button type="button" aria-label="Снять фильтр даты" onClick={onClearPlanDate}>
              ✕
            </button>
          </span>
        )}
        <label className="admin-plans-check">
          <input
            type="checkbox"
            checked={futureOnly}
            onChange={(event) => {
              setFutureOnly(event.target.checked);
              setOffset(0);
            }}
          />
          Только будущие
        </label>
        <select
          className="input"
          value={location}
          aria-label="Локация"
          onChange={(event) => {
            setLocation(event.target.value);
            setOffset(0);
          }}
        >
          <option value="">Все локации</option>
          {locations.map((item) => (
            <option key={item.key} value={item.key}>
              {item.name} ({item.count})
            </option>
          ))}
        </select>
        <form className="admin-plans-person" onSubmit={submitPerson}>
          <input
            className="input"
            type="search"
            placeholder="Имя, @ник или №"
            value={personInput}
            aria-label="Человек"
            onChange={(event) => {
              setPersonInput(event.target.value);
              if (!event.target.value) {
                setPerson("");
                setOffset(0);
              }
            }}
          />
          <button type="submit" className="btn btn-sm">
            Найти
          </button>
        </form>
      </div>

      {error && <p className="error-text">{error}</p>}
      {!data && !error && <p className="muted">Загрузка…</p>}
      {data && (
        <>
          <div className="table-scroll">
            <table className="data-table admin-plans-table admin-plans-feed">
              <thead>
                <tr>
                  <th>Поставил</th>
                  <th>Кто</th>
                  <th>Что</th>
                  <th>Дата</th>
                  <th>Локация</th>
                  <th>Видно</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => {
                  const what = feedWhat(item);
                  return (
                    <tr key={item.id} className={item.is_public ? undefined : "admin-plans-row-private"}>
                      <td className="num admin-plans-nowrap">{shortMoment(item.at)}</td>
                      <td>
                        {item.user_serial_id ? (
                          <a href={`/users/${item.user_serial_id}`}>{item.user_label}</a>
                        ) : (
                          item.user_label
                        )}
                      </td>
                      <td>
                        <span className={`admin-plans-kind admin-plans-kind-${what.tone}`}>{what.text}</span>
                      </td>
                      <td className="admin-plans-nowrap">{feedDate(item)}</td>
                      <td>
                        {item.location_name ? (
                          item.location_slug ? (
                            <a href={`/locations/${item.location_slug}`}>{item.location_name}</a>
                          ) : (
                            item.location_name
                          )
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td className="admin-plans-nowrap">
                        {item.is_public ? (
                          <span className={item.profile_private ? "muted" : undefined}>
                            публично{item.profile_private ? " · профиль закрыт" : ""}
                          </span>
                        ) : (
                          <span className="admin-plans-private">🔒 приватно</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
                {data.items.length === 0 && (
                  <tr>
                    <td colSpan={6} className="muted">
                      Ничего не нашлось.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {pages > 1 && (
            <div className="actions-row admin-plans-pager">
              <button type="button" className="btn btn-sm" disabled={page <= 1} onClick={() => setOffset(offset - FEED_LIMIT)}>
                ← Назад
              </button>
              <span className="muted">
                {page} / {pages}
              </span>
              <button
                type="button"
                className="btn btn-sm"
                disabled={page >= pages}
                onClick={() => setOffset(offset + FEED_LIMIT)}
              >
                Вперёд →
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function AdminPlansContent() {
  const [periodDays, setPeriodDays] = useState<number>(30);
  const [data, setData] = useState<AdminPlansOverviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const [day, setDay] = useState<string | null>(dayFromHash);
  const [feedDate, setFeedDate] = useState<string | null>(null);

  // Якорь #day=… — и при открытии ссылки, и при правке адреса руками.
  useEffect(() => {
    const onHash = () => setDay(dayFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const selectDay = useCallback((isoDate: string | null) => {
    setDay(isoDate);
    const url = `${window.location.pathname}${window.location.search}${isoDate ? `#day=${isoDate}` : ""}`;
    window.history.replaceState(window.history.state, "", url);
  }, []);

  const showFeed = useCallback((isoDate: string) => {
    setFeedDate(isoDate);
    document.getElementById(FEED_ID)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  const load = useCallback(async (days: number) => {
    const current = ++seq.current;
    setError(null);
    try {
      const payload = await getAdminPlans(days);
      if (current === seq.current) setData(payload);
    } catch (err) {
      if (current === seq.current) setError(err instanceof Error ? err.message : "Не удалось загрузить планы");
    }
  }, []);

  useEffect(() => {
    void load(periodDays);
  }, [load, periodDays]);

  return (
    <AdminShell title="Планы">
      <AdminSubnav activePath="/admin/plans" />
      <div className="admin-stats-toolbar">
        <div className="admin-stats-periods" role="tablist" aria-label="Период динамики">
          {PERIODS.map((days) => (
            <button
              key={days}
              type="button"
              className={periodDays === days ? "btn primary" : "btn secondary"}
              onClick={() => setPeriodDays(days)}
            >
              {days} дней
            </button>
          ))}
        </div>
        {data && <span className="muted admin-stats-generated">Обновлено: {formatDateTime(data.generated_at)}</span>}
      </div>
      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}
      {!data && !error && <p className="muted">Загрузка…</p>}
      {data && (
        <Overview
          data={data}
          day={day}
          onSelectDay={selectDay}
          onCloseDay={() => selectDay(null)}
          onShowFeed={showFeed}
        />
      )}
      {data && <Feed locations={data.locations} planDate={feedDate} onClearPlanDate={() => setFeedDate(null)} />}
    </AdminShell>
  );
}

export function AdminPlansPage() {
  return (
    <RequireAdmin>
      <AdminPlansContent />
    </RequireAdmin>
  );
}
