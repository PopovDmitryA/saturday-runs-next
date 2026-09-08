import { useCallback, useEffect, useMemo, useState } from "react";
import { DetailModal } from "../../components/DetailModal";
import {
  ApiError,
  cancelVolunteerSignup,
  createVolunteerSignup,
  getVolunteerSignupOptions,
  type VolunteerSignupOptions,
  type VolunteerSignupRequestItem,
} from "../../lib/api";
import { formatDate } from "../../lib/format";
import { PromoLoginCard } from "../../components/PromoLoginCard";
import { useOptionalUser } from "../../lib/useOptionalUser";

// Заявка на волонтёрство со страницы локации (решение Дмитрия 04.09.2026):
// участник выбирает дату и роль, заявка уходит организатору в Telegram, тот
// подтверждает её в кабинете и вносит человека в NRMS. Даты и роли — из
// открытой записи 5verst.ru, поэтому видно, какие клетки уже заняты.

const WEEKDAYS_RU = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];

function dateWithWeekday(iso: string): string {
  const parsed = new Date(`${iso}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return iso;
  }
  return `${WEEKDAYS_RU[parsed.getDay()]} ${formatDate(iso)}`;
}

// «Фотограф — свободно 1 из 3, уже Анна, Пётр»: места считаем по строкам записи.
function roleOptionLabel(item: {
  name: string;
  takenBy: string | null;
  slots: number;
  free: number;
}): string {
  if (item.takenBy) {
    const freePart =
      item.free > 0 ? `свободно ${item.free} из ${item.slots}, ` : "";
    return `${item.name} — ${freePart}уже ${item.takenBy}`;
  }
  return item.slots > 1 ? `${item.name} — свободно ${item.slots}` : item.name;
}

export function signupStatusLabel(item: VolunteerSignupRequestItem): {
  text: string;
  tone: "wait" | "ok" | "no" | "muted";
} {
  switch (item.status) {
    case "pending":
      return { text: "ждёт ответа организатора", tone: "wait" };
    case "confirmed":
      if (
        item.nrms_status === "saved" ||
        item.nrms_status === "manual" ||
        item.in_open_roster
      ) {
        return { text: "вы в записи", tone: "ok" };
      }
      return { text: "подтверждена, организатор внесёт в запись", tone: "ok" };
    case "declined":
      return { text: "организатор не смог принять", tone: "no" };
    default:
      return { text: "отозвана", tone: "muted" };
  }
}

function MyRequests({
  items,
  onCancel,
  busyId,
}: {
  items: VolunteerSignupRequestItem[];
  onCancel: (item: VolunteerSignupRequestItem) => void;
  busyId: string | null;
}) {
  if (items.length === 0) {
    return null;
  }
  return (
    <div className="vs-my">
      <h3 className="vs-subtitle">Ваши заявки</h3>
      <ul className="vs-my-list">
        {items.map((item) => {
          const status = signupStatusLabel(item);
          return (
            <li key={item.id} className="vs-my-item">
              <div className="vs-my-main">
                <span className="vs-my-when">
                  {dateWithWeekday(item.event_date)}
                </span>
                <span className="vs-my-role">{item.role_name}</span>
              </div>
              <div className="vs-my-side">
                <span className={`vs-status vs-status-${status.tone}`}>
                  {status.text}
                </span>
                {item.decision_note && (
                  <span className="muted vs-note">«{item.decision_note}»</span>
                )}
                {item.status === "pending" && (
                  <button
                    type="button"
                    className="btn secondary btn-compact"
                    disabled={busyId === item.id}
                    onClick={() => onCancel(item)}
                  >
                    Отозвать
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function CopyFallback({
  text,
  chatHint,
}: {
  text: string;
  chatHint: string | null;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="vs-fallback">
      <p>
        Организатор этой локации ещё не заходил на сайт, поэтому заявке некуда
        прийти. Напишите оргкоманде напрямую — вот готовое сообщение:
      </p>
      <blockquote className="vs-fallback-text">{text}</blockquote>
      <div className="vs-actions">
        <button
          type="button"
          className="btn secondary"
          onClick={() => {
            navigator.clipboard
              ?.writeText(text)
              .then(() => setCopied(true))
              .catch(() => setCopied(false));
          }}
        >
          {copied ? "Скопировано" : "Скопировать"}
        </button>
        {chatHint && (
          <a
            className="btn secondary"
            href={chatHint}
            target="_blank"
            rel="noreferrer"
          >
            Открыть запись 5 вёрст
          </a>
        )}
      </div>
    </div>
  );
}

export function VolunteerSignupModal({
  slug,
  open,
  onClose,
}: {
  slug: string;
  open: boolean;
  onClose: () => void;
}) {
  const user = useOptionalUser();
  const [options, setOptions] = useState<VolunteerSignupOptions | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [date, setDate] = useState("");
  const [role, setRole] = useState("");
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [done, setDone] = useState<VolunteerSignupRequestItem | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    return getVolunteerSignupOptions(slug)
      .then((payload) => {
        setOptions(payload);
        setDate((current) => current || payload.dates[0]?.date || "");
      })
      .catch((err) => {
        setError(
          err instanceof Error ? err.message : "Не удалось загрузить запись",
        );
      })
      .finally(() => setLoading(false));
  }, [slug]);

  useEffect(() => {
    if (!open || !user) {
      return;
    }
    setDone(null);
    setSubmitError(null);
    void load();
  }, [open, user, load]);

  const selectedDateLabel = useMemo(
    () => options?.dates.find((item) => item.date === date)?.date_display ?? "",
    [options, date],
  );

  // Роль: сначала свободные на выбранную дату, занятые — с именем, кто уже стоит.
  const roleOptions = useMemo(() => {
    if (!options) {
      return [];
    }
    return options.roles.map((item) => {
      const takenBy = selectedDateLabel
        ? item.filled[selectedDateLabel]
        : undefined;
      const taken = takenBy ? takenBy.split(", ").length : 0;
      const free = Math.max(item.slots - taken, 0);
      return {
        name: item.name,
        takenBy: takenBy ?? null,
        slots: item.slots,
        free,
      };
    });
  }, [options, selectedDateLabel]);

  useEffect(() => {
    if (roleOptions.length === 0) {
      return;
    }
    setRole((current) => {
      if (current && roleOptions.some((item) => item.name === current)) {
        return current;
      }
      const free = roleOptions.find((item) => item.free > 0);
      return (free ?? roleOptions[0]).name;
    });
  }, [roleOptions]);

  const submit = () => {
    if (!date || !role) {
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    createVolunteerSignup(slug, { event_date: date, role_name: role, comment })
      .then((payload) => {
        setDone(payload.item);
        setComment("");
        return load();
      })
      .catch((err) => {
        setSubmitError(
          err instanceof ApiError
            ? err.message
            : "Не удалось отправить заявку, попробуйте ещё раз",
        );
      })
      .finally(() => setSubmitting(false));
  };

  const cancel = (item: VolunteerSignupRequestItem) => {
    setBusyId(item.id);
    cancelVolunteerSignup(slug, item.id)
      .then(() => load())
      .catch((err) => {
        setSubmitError(
          err instanceof ApiError ? err.message : "Не удалось отозвать заявку",
        );
      })
      .finally(() => setBusyId(null));
  };

  const body = (() => {
    if (user === null) {
      return (
        <PromoLoginCard
          icon="🙋"
          title="Войдите, чтобы записаться"
          text="Заявка уходит организатору с вашим ID 5 вёрст — для этого нужен вход на сайт и привязанный профиль."
        />
      );
    }
    if (user === undefined || (loading && !options)) {
      return <p className="muted">Загружаем запись…</p>;
    }
    if (error) {
      return <p className="error-text">{error}</p>;
    }
    if (!options) {
      return null;
    }
    if (!options.supported) {
      return (
        <p className="muted">
          Запись через сайт пока работает только для локаций 5 вёрст: у них есть
          открытая запись волонтёров и система учёта, куда организатор вносит
          состав.
        </p>
      );
    }
    if (!options.linked) {
      return (
        <div className="vs-block">
          <p>
            Организатору нужен ваш ID 5 вёрст, чтобы внести вас в запись без
            путаницы с тёзками. Привяжите профиль 5 вёрст в настройках — и
            возвращайтесь.
          </p>
          <a className="btn" href="/settings">
            К настройкам
          </a>
        </div>
      );
    }
    if (!options.organizer_connected) {
      return (
        <>
          <CopyFallback
            text={options.fallback_message}
            chatHint={options.roster_url}
          />
          <MyRequests
            items={options.my_requests}
            onCancel={cancel}
            busyId={busyId}
          />
        </>
      );
    }
    return (
      <>
        {done && (
          <div className="vs-done">
            <strong>Заявка отправлена.</strong> Организатор получит её в
            Telegram, а вы — ответ там же, как только он примет решение.
          </div>
        )}
        <form
          className="vs-form"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <label className="vs-field">
            <span className="vs-label">Дата</span>
            <select
              className="vs-select"
              value={date}
              onChange={(event) => setDate(event.target.value)}
            >
              {options.dates.map((item) => (
                <option key={item.date} value={item.date}>
                  {dateWithWeekday(item.date)}
                </option>
              ))}
            </select>
          </label>
          <label className="vs-field">
            <span className="vs-label">Роль</span>
            <select
              className="vs-select"
              value={role}
              onChange={(event) => setRole(event.target.value)}
            >
              {roleOptions.map((item) => (
                <option key={item.name} value={item.name}>
                  {roleOptionLabel(item)}
                </option>
              ))}
            </select>
            {options.roster_available ? (
              <span className="muted vs-hint">
                Занятость ролей — по открытой записи 5 вёрст. На занятую роль
                тоже можно записаться: на многих позициях нужны несколько
                человек.
              </span>
            ) : (
              <span className="muted vs-hint">
                Открытая запись локации сейчас недоступна — показан общий список
                ролей.
              </span>
            )}
          </label>
          <label className="vs-field">
            <span className="vs-label">
              Комментарий организатору (необязательно)
            </span>
            <textarea
              className="vs-textarea"
              rows={2}
              maxLength={500}
              value={comment}
              placeholder="Например: впервые в этой роли, подскажите, куда подойти"
              onChange={(event) => setComment(event.target.value)}
            />
          </label>
          <p className="muted vs-hint">
            Организатор увидит имя{" "}
            {options.participant_name ? `«${options.participant_name}»` : ""} и
            ID {options.verst_id}.
          </p>
          {submitError && <p className="error-text">{submitError}</p>}
          <div className="vs-actions">
            <button
              type="submit"
              className="btn"
              disabled={submitting || !date || !role}
            >
              {submitting ? "Отправляем…" : "Отправить заявку"}
            </button>
            {options.roster_url && (
              <a
                className="muted vs-link"
                href={options.roster_url}
                target="_blank"
                rel="noreferrer"
              >
                Открытая запись 5 вёрст ↗
              </a>
            )}
          </div>
        </form>
        <MyRequests
          items={options.my_requests}
          onCancel={cancel}
          busyId={busyId}
        />
      </>
    );
  })();

  return (
    <DetailModal open={open} title="Хочу волонтёрить" onClose={onClose}>
      <div className="vs-modal">{body}</div>
    </DetailModal>
  );
}
