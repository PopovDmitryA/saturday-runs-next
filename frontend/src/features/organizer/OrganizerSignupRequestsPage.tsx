import { useCallback, useEffect, useState } from "react";
import { RequireAuth } from "../../components/RequireAuth";
import {
  ApiError,
  decideOrganizerSignupRequests,
  getOrganizerNrmsSession,
  getOrganizerSignupRequests,
  organizerNrmsLogin,
  organizerNrmsLogout,
  type OrganizerNrmsSession,
  type OrganizerSignupRequestsResponse,
  type VolunteerSignupRequestItem,
} from "../../lib/api";
import { formatDateTime, formatInt } from "../../lib/format";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { locationHintFor, rememberLocationHint } from "../../lib/locationHint";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { OrganizerBreadcrumbs } from "./OrganizerBreadcrumbs";
import { OrganizerDenied } from "./OrganizerDenied";
import "./organizer.css";

// Заявки на волонтёрство (решение Дмитрия 04.09.2026): участник подаёт заявку
// на странице локации, организатор здесь подтверждает или отклоняет. При
// подтверждении сайт вносит человека в NRMS под сессией организатора — для
// этого организатор один раз за ~4 часа входит в NRMS прямо тут. Пароль на
// сайте не хранится, только токен NRMS на срок его жизни.

const WEEKDAYS_RU = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];

function dateWithWeekday(iso: string, display: string): string {
  const parsed = new Date(`${iso}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return display;
  }
  return `${WEEKDAYS_RU[parsed.getDay()]} ${display}`;
}

function nrmsLine(
  item: VolunteerSignupRequestItem,
): { text: string; tone: string } | null {
  if (item.status !== "confirmed") {
    return null;
  }
  if (item.nrms_status === "saved") {
    return { text: "внесён в NRMS сайтом", tone: "ok" };
  }
  if (item.nrms_status === "manual" || item.in_open_roster) {
    return { text: "есть в открытой записи 5 вёрст", tone: "ok" };
  }
  if (item.nrms_status === "failed") {
    return {
      text: `в NRMS не внесён: ${item.nrms_error ?? "ошибка"}`,
      tone: "no",
    };
  }
  return {
    text: "в NRMS пока не внесён — внесите вручную по ID",
    tone: "wait",
  };
}

function StatusBadge({ item }: { item: VolunteerSignupRequestItem }) {
  switch (item.status) {
    case "pending":
      return <span className="org-badge org-badge-pb">ждёт решения</span>;
    case "confirmed":
      return <span className="org-badge org-badge-role">подтверждена</span>;
    case "declined":
      return <span className="org-badge org-badge-new">отклонена</span>;
    default:
      return <span className="org-badge">отозвана участником</span>;
  }
}

function NrmsPanel({
  slug,
  session,
  onChange,
}: {
  slug: string;
  session: OrganizerNrmsSession | null;
  onChange: (session: OrganizerNrmsSession) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  if (session === null) {
    return (
      <section className="card org-signup-nrms">
        <p className="muted">Проверяем сессию NRMS…</p>
      </section>
    );
  }

  if (session.connected) {
    return (
      <section className="card org-signup-nrms">
        <div className="org-signup-nrms-row">
          <div>
            <strong>NRMS подключён</strong>
            <span className="muted">
              {" "}
              · {session.username}
              {session.expires_at
                ? ` · до ${formatDateTime(session.expires_at)}`
                : ""}
            </span>
            <p className="muted org-signup-nrms-hint">
              Подтверждённые заявки будут вноситься в запись NRMS вашей учёткой.
              Когда сессия истечёт, попросим войти заново.
            </p>
          </div>
          <button
            type="button"
            className="btn secondary"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              organizerNrmsLogout(slug)
                .then((state) => onChange(state))
                .finally(() => setBusy(false));
            }}
          >
            Выйти из NRMS
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="card org-signup-nrms">
      <div className="org-signup-nrms-row">
        <div>
          <strong>NRMS не подключён</strong>
          <p className="muted org-signup-nrms-hint">
            Без входа заявки можно подтверждать, но в запись NRMS их придётся
            вносить руками. Войдите — и сайт сделает это сам. Пароль мы не
            сохраняем: он уходит в NRMS и забывается, остаётся только токен
            сессии на несколько часов.
          </p>
        </div>
        {!open && (
          <button type="button" className="btn" onClick={() => setOpen(true)}>
            Войти в NRMS
          </button>
        )}
      </div>
      {open && (
        <form
          className="org-signup-nrms-form"
          onSubmit={(event) => {
            event.preventDefault();
            setBusy(true);
            setError(null);
            organizerNrmsLogin(slug, username.trim(), password)
              .then((state) => {
                setPassword("");
                setOpen(false);
                onChange(state);
              })
              .catch((err) => {
                setError(
                  err instanceof ApiError
                    ? err.message
                    : "Не удалось войти в NRMS",
                );
              })
              .finally(() => setBusy(false));
          }}
        >
          <label className="org-signup-field">
            <span>Логин NRMS</span>
            <input
              type="text"
              autoComplete="username"
              placeholder="A790103773"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </label>
          <label className="org-signup-field">
            <span>Пароль NRMS</span>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          {error && <p className="error-text">{error}</p>}
          <div className="org-signup-actions">
            <button
              type="submit"
              className="btn"
              disabled={busy || !username || !password}
            >
              {busy ? "Входим…" : "Войти"}
            </button>
            <button
              type="button"
              className="btn secondary"
              disabled={busy}
              onClick={() => {
                setOpen(false);
                setPassword("");
                setError(null);
              }}
            >
              Отмена
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

type Choice = "confirmed" | "declined" | null;

function RequestCard({
  item,
  choice,
  note,
  onChoice,
  onNote,
  disabled,
}: {
  item: VolunteerSignupRequestItem;
  choice: Choice;
  note: string;
  onChoice: (choice: Choice) => void;
  onNote: (note: string) => void;
  disabled: boolean;
}) {
  const option = (value: Choice, label: string) => (
    <button
      type="button"
      role="radio"
      aria-checked={choice === value}
      className={
        choice === value
          ? `org-signup-choice org-signup-choice-active org-signup-choice-${value ?? "skip"}`
          : "org-signup-choice"
      }
      disabled={disabled}
      onClick={() => onChoice(value)}
    >
      {label}
    </button>
  );
  return (
    <li
      className={`card org-signup-item${choice ? ` org-signup-item-${choice}` : ""}`}
    >
      <div className="org-signup-item-head">
        <div>
          <div className="org-signup-item-who">
            <strong>{item.participant_name ?? "Участник"}</strong>
            <span className="muted"> · ID {item.verst_id}</span>
          </div>
          <div className="org-signup-item-when">
            {dateWithWeekday(item.event_date, item.event_date_display)} ·{" "}
            {item.role_name}
          </div>
          {item.comment && (
            <p className="org-signup-item-comment">«{item.comment}»</p>
          )}
          <span className="muted org-signup-item-meta">
            Подана {formatDateTime(item.created_at)}
            {item.organizer_notified
              ? ""
              : " · уведомление в Telegram не доставлено"}
          </span>
        </div>
        <StatusBadge item={item} />
      </div>
      <div
        className="org-signup-choices"
        role="radiogroup"
        aria-label="Решение по заявке"
      >
        {option("confirmed", "Подтвердить")}
        {option("declined", "Отклонить")}
        {option(null, "Пока не решать")}
      </div>
      {choice === "declined" && (
        <label className="org-signup-field">
          <span>Почему не получается (участник увидит это в Telegram)</span>
          <input
            type="text"
            maxLength={500}
            placeholder="Например: маршалов на эту дату уже хватает"
            value={note}
            disabled={disabled}
            onChange={(event) => onNote(event.target.value)}
          />
        </label>
      )}
    </li>
  );
}

function OrganizerSignupContent({ slug }: { slug: string }) {
  const [data, setData] = useState<OrganizerSignupRequestsResponse | null>(
    null,
  );
  const [session, setSession] = useState<OrganizerNrmsSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [saving, setSaving] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  // Решения по карточкам копятся здесь и уходят одним запросом («Сохранить»).
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [lastResults, setLastResults] = useState<VolunteerSignupRequestItem[]>(
    [],
  );

  const load = useCallback(() => {
    return getOrganizerSignupRequests(slug)
      .then((payload) => {
        setData(payload);
        setError(null);
        rememberLocationHint({
          slug: payload.location.slug,
          name: payload.location.name,
        });
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 403) {
          setForbidden(true);
        } else if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError(
            err instanceof Error ? err.message : "Не удалось загрузить заявки",
          );
        }
      });
  }, [slug]);

  useEffect(() => {
    void load();
    getOrganizerNrmsSession(slug)
      .then(setSession)
      .catch(() =>
        setSession({ connected: false, username: null, expires_at: null }),
      );
  }, [slug, load]);

  const pending = (data?.items ?? []).filter(
    (item) => item.status === "pending",
  );
  const history = (data?.items ?? [])
    .filter((item) => item.status !== "pending")
    .reverse();
  const nrmsConnected = Boolean(session?.connected);
  const decided = pending.filter((item) => choices[item.id]);
  const confirmCount = decided.filter(
    (item) => choices[item.id] === "confirmed",
  ).length;
  const declineCount = decided.length - confirmCount;

  const save = () => {
    if (decided.length === 0) {
      return;
    }
    setSaving(true);
    setActionError(null);
    decideOrganizerSignupRequests(
      slug,
      decided.map((item) => ({
        request_id: item.id,
        decision: choices[item.id] as "confirmed" | "declined",
        note: choices[item.id] === "declined" ? notes[item.id] : null,
      })),
    )
      .then((payload) => {
        setLastResults(payload.items);
        setChoices({});
        setNotes({});
        // Протухшая сессия NRMS сбрасывается на сервере — перечитываем её состояние.
        if (payload.items.some((item) => item.nrms_status === "failed")) {
          getOrganizerNrmsSession(slug)
            .then(setSession)
            .catch(() => undefined);
        }
        return load();
      })
      .catch((err) => {
        setActionError(
          err instanceof ApiError
            ? err.message
            : "Не удалось сохранить решения",
        );
      })
      .finally(() => setSaving(false));
  };

  const name = data?.location.name ?? locationHintFor(slug)?.name ?? null;
  const sidebar = {
    active: "organizer" as const,
    location: name ? { slug, name } : locationHintFor(slug),
  };

  if (forbidden || notFound) {
    return (
      <PortalSectionShell sidebar={sidebar}>
        <OrganizerDenied slug={slug} notFound={notFound} />
      </PortalSectionShell>
    );
  }

  return (
    <PortalSectionShell sidebar={sidebar}>
      <header className="loc-header">
        <OrganizerBreadcrumbs
          slug={slug}
          locationName={name}
          tool="Заявки на волонтёрство"
        />
        <div className="loc-header-title">
          <h1>{name ?? "Локация"} — заявки на волонтёрство</h1>
        </div>
        <p className="muted">
          Участники просятся в оргкоманду со страницы локации: дата, роль и ID 5
          вёрст. Заявка приходит вам в Telegram, а решение принимается здесь —
          участник получит ответ в Telegram.
        </p>
      </header>

      {data && !data.supported && (
        <div className="org-notice">
          <span className="org-notice-icon" aria-hidden="true">
            ℹ️
          </span>
          <div className="org-notice-text">
            У этой локации нет половины 5 вёрст — запись через сайт пока
            работает только для них.
          </div>
        </div>
      )}

      <NrmsPanel slug={slug} session={session} onChange={setSession} />

      {error && <p className="error-text">{error}</p>}
      {actionError && <p className="error-text">{actionError}</p>}
      {lastResults.length > 0 && (
        <div className="org-notice org-signup-result">
          <span className="org-notice-icon" aria-hidden="true">
            📝
          </span>
          <div className="org-notice-text">
            <strong>Решения сохранены.</strong>
            <ul className="org-signup-result-list">
              {lastResults.map((item) => {
                const line = nrmsLine(item);
                return (
                  <li key={item.id}>
                    {item.participant_name ?? "Участник"} ·{" "}
                    {item.event_date_display} · {item.role_name}:{" "}
                    {item.status === "confirmed" ? "подтверждена" : "отклонена"}
                    {line ? <>, {line.text}</> : null}
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      )}

      <section className="org-signup-section">
        <h2 className="section-title">
          Ждут решения{data ? ` (${formatInt(pending.length)})` : ""}
        </h2>
        {data === null ? (
          <p className="muted">Загрузка…</p>
        ) : pending.length === 0 ? (
          <p className="muted">
            Новых заявок нет. Появятся — придёт сообщение в Telegram.
          </p>
        ) : (
          <>
            <p className="muted">
              Отметьте решение по каждой заявке и нажмите «Сохранить»:
              подтверждённые на одну дату уходят в NRMS одной записью.
            </p>
            <ul className="org-signup-list">
              {pending.map((item) => (
                <RequestCard
                  key={item.id}
                  item={item}
                  choice={choices[item.id] ?? null}
                  note={notes[item.id] ?? ""}
                  disabled={saving}
                  onChoice={(choice) =>
                    setChoices((current) => ({ ...current, [item.id]: choice }))
                  }
                  onNote={(note) =>
                    setNotes((current) => ({ ...current, [item.id]: note }))
                  }
                />
              ))}
            </ul>
            <div className="org-signup-savebar">
              <button
                type="button"
                className="btn"
                disabled={saving || decided.length === 0}
                onClick={save}
              >
                {saving
                  ? "Сохраняем…"
                  : decided.length === 0
                    ? "Сохранить"
                    : `Сохранить: ${confirmCount} подтвердить, ${declineCount} отклонить`}
              </button>
              <span className="muted">
                {nrmsConnected
                  ? "Подтверждённые будут внесены в NRMS."
                  : "NRMS не подключён: подтверждённых придётся вносить в NRMS вручную."}
              </span>
            </div>
          </>
        )}
      </section>

      {history.length > 0 && (
        <section className="org-signup-section">
          <h2 className="section-title">Решённые</h2>
          <ul className="org-signup-list org-signup-list-history">
            {history.map((item) => {
              const line = nrmsLine(item);
              return (
                <li
                  key={item.id}
                  className="card org-signup-item org-signup-item-history"
                >
                  <div className="org-signup-item-head">
                    <div>
                      <div className="org-signup-item-who">
                        <strong>{item.participant_name ?? "Участник"}</strong>
                        <span className="muted"> · ID {item.verst_id}</span>
                      </div>
                      <div className="org-signup-item-when">
                        {dateWithWeekday(
                          item.event_date,
                          item.event_date_display,
                        )}{" "}
                        · {item.role_name}
                      </div>
                      {line && (
                        <span
                          className={`org-signup-nrms-line org-signup-tone-${line.tone}`}
                        >
                          {line.text}
                        </span>
                      )}
                      {item.decision_note && (
                        <span className="muted org-signup-item-meta">
                          Комментарий: {item.decision_note}
                        </span>
                      )}
                    </div>
                    <StatusBadge item={item} />
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {data?.roster_url && (
        <p className="muted org-signup-footnote">
          Сверяем подтверждённые заявки с{" "}
          <a href={data.roster_url} target="_blank" rel="noreferrer">
            открытой записью 5 вёрст
          </a>{" "}
          и со снимком состава NRMS: если имя появилось в клетке роли, заявка
          считается внесённой, даже когда её вписали вручную.
        </p>
      )}
    </PortalSectionShell>
  );
}

export function OrganizerSignupRequestsPage({ slug }: { slug: string }) {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {() => <OrganizerSignupContent slug={slug} />}
    </RequireAuth>
  );
}
