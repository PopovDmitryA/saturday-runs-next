import { useEffect, useMemo, useState, type MouseEvent } from "react";
import { PlatformBadge } from "../../components/PlatformBadge";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import {
  type CoRunnerItem,
  type CoRunnerMeetingItem,
} from "../../lib/api";
import { formatDate, formatDuration, platformCodeLabel, pluralizeRu } from "../../lib/format";

function SiteProfileIcon() {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
      <circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="10" cy="8.2" r="2.3" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path
        d="M5.3 14.8c1-2.1 3-3.2 4.7-3.2s3.7 1.1 4.7 3.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

function stopPropagation(event: MouseEvent) {
  event.stopPropagation();
}

function scoreLabel(item: CoRunnerItem): string {
  if (item.timed_meetings === 0) {
    return "—";
  }
  return `${item.my_wins} : ${item.their_wins}`;
}

function scoreTone(item: CoRunnerItem): string {
  if (item.timed_meetings === 0 || item.my_wins === item.their_wins) {
    return "";
  }
  return item.my_wins > item.their_wins ? " co-runners-score-win" : " co-runners-score-loss";
}

function meetingOutcome(meeting: CoRunnerMeetingItem): {
  label: string;
  tone: string;
} {
  if (meeting.my_time_sec == null || meeting.their_time_sec == null) {
    return { label: "—", tone: "" };
  }
  if (meeting.my_time_sec < meeting.their_time_sec) {
    return { label: "Вы быстрее", tone: " co-runners-score-win" };
  }
  if (meeting.my_time_sec > meeting.their_time_sec) {
    return { label: "Участник быстрее", tone: " co-runners-score-loss" };
  }
  if (meeting.my_position != null && meeting.their_position != null && meeting.my_position !== meeting.their_position) {
    return meeting.my_position < meeting.their_position
      ? { label: "Вы быстрее", tone: " co-runners-score-win" }
      : { label: "Участник быстрее", tone: " co-runners-score-loss" };
  }
  return { label: "Вровень", tone: "" };
}

function ParticipantName({ item }: { item: CoRunnerItem }) {
  const name = item.display_name ?? "Без имени";

  return (
    <span className="co-runners-name">
      <span>{name}</span>
      {item.site_serial_id != null && (
        <StatHintTooltip
          text="Откроется профиль участника на этом сайте"
          className="co-runners-profile-icon co-runners-profile-icon-site"
        >
          <a href={`/users/${item.site_serial_id}`} onClick={stopPropagation} aria-label="Профиль на сайте">
            <SiteProfileIcon />
          </a>
        </StatHintTooltip>
      )}
    </span>
  );
}

function timeWithPosition(timeSec: number | null, position: number | null): string {
  if (timeSec == null) {
    return "—";
  }
  const time = formatDuration(timeSec);
  return position != null ? `${time} · ${position} место` : time;
}

function MeetingsDetail({ meetings }: { meetings: CoRunnerMeetingItem[] }) {
  return (
    <table className="data-table co-runners-meetings-table">
      <thead>
        <tr>
          <th>Дата</th>
          <th>Локация</th>
          <th>Ваш результат</th>
          <th>Результат участника</th>
          <th>Итог</th>
        </tr>
      </thead>
      <tbody>
        {meetings.map((meeting) => {
          const outcome = meetingOutcome(meeting);
          return (
            <tr key={`${meeting.event_date}-${meeting.platform_code}-${meeting.location_name}`}>
              <td>
                {meeting.event_url ? (
                  <a href={meeting.event_url} target="_blank" rel="noreferrer">
                    {formatDate(meeting.event_date)}
                  </a>
                ) : (
                  formatDate(meeting.event_date)
                )}
              </td>
              <td>
                {meeting.location_name} <PlatformBadge code={meeting.platform_code} />
              </td>
              <td className="co-runners-score">
                {timeWithPosition(meeting.my_time_sec, meeting.my_position)}
              </td>
              <td className="co-runners-score">
                {timeWithPosition(meeting.their_time_sec, meeting.their_position)}
              </td>
              <td className={`co-runners-score${outcome.tone}`}>{outcome.label}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

type CoRunnersContentProps = {
  load: () => Promise<CoRunnerItem[]>;
  loadMeetings: (participantKey: string) => Promise<CoRunnerMeetingItem[]>;
};

export function CoRunnersContent({ load, loadMeetings }: CoRunnersContentProps) {
  const [items, setItems] = useState<CoRunnerItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const [meetingsByKey, setMeetingsByKey] = useState<Record<string, CoRunnerMeetingItem[]>>({});
  const [meetingsLoadingKey, setMeetingsLoadingKey] = useState<string | null>(null);
  const [meetingsError, setMeetingsError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    load()
      .then((data) => {
        if (!cancelled) {
          setItems(data);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError("Не удалось загрузить список встреч. Попробуйте обновить страницу.");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [load]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) {
      return items;
    }
    return items.filter((item) => (item.display_name ?? "").toLowerCase().includes(needle));
  }, [items, query]);

  const toggleRow = (key: string) => {
    if (expandedKey === key) {
      setExpandedKey(null);
      return;
    }
    setExpandedKey(key);
    setMeetingsError(null);
    if (!meetingsByKey[key]) {
      setMeetingsLoadingKey(key);
      loadMeetings(key)
        .then((data) => {
          setMeetingsByKey((prev) => ({ ...prev, [key]: data }));
        })
        .catch(() => {
          setMeetingsError("Не удалось загрузить детали встреч.");
        })
        .finally(() => {
          setMeetingsLoadingKey((prev) => (prev === key ? null : prev));
        });
    }
  };

  const pageBody = (
    <>
      <div className="card">
        <h2 className="section-title">Встречи на стартах</h2>
        <p className="muted co-runners-intro">
          Участники, с которыми вы чаще всего оказывались в одном протоколе. Личный счёт —
          сколько раз каждый финишировал первым из вас двоих: слева ваши финиши. Нажмите на
          строку, чтобы увидеть каждую встречу.
        </p>

        {loading && <p className="muted">Загрузка…</p>}
        {error && <p className="error-text">{error}</p>}

        {!loading && !error && items.length === 0 && (
          <p className="muted">Пока нет пересечений — нужны пробежки с протоколами.</p>
        )}

        {!loading && !error && items.length > 0 && (
          <>
            <input
              className="input co-runners-search"
              type="search"
              placeholder="Поиск по имени"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <div className="table-scroll">
              <table className="data-table co-runners-table">
                <thead>
                  <tr>
                    <th className="td-num">#</th>
                    <th>Участник</th>
                    <th>Система</th>
                    <th className="td-num">Встреч</th>
                    <th className="td-num">Счёт</th>
                    <th>Первая встреча</th>
                    <th>Последняя встреча</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((item, index) => {
                    const expanded = expandedKey === item.participant_key;
                    return [
                      <tr
                        key={item.participant_key}
                        className={`co-runners-row${expanded ? " co-runners-row-expanded" : ""}`}
                        onClick={() => toggleRow(item.participant_key)}
                      >
                        <td className="td-num muted">{index + 1}</td>
                        <td>
                          <span
                            className={`co-runners-caret${expanded ? " co-runners-caret-open" : ""}`}
                            aria-hidden="true"
                          />
                          <ParticipantName item={item} />
                        </td>
                        <td>
                          <span className="co-runners-badges">
                            {item.platform_codes.map((code) => {
                              const profileUrl = item.profile_urls[code];
                              const badge = <PlatformBadge code={code} />;
                              if (!profileUrl) {
                                return <span key={code}>{badge}</span>;
                              }
                              return (
                                <StatHintTooltip
                                  key={code}
                                  text={`Откроется профиль в ${platformCodeLabel(code)} в новой вкладке`}
                                  className="co-runners-badge-link"
                                >
                                  <a
                                    href={profileUrl}
                                    target="_blank"
                                    rel="noreferrer"
                                    onClick={stopPropagation}
                                    aria-label={`Профиль в ${platformCodeLabel(code)}`}
                                  >
                                    {badge}
                                  </a>
                                </StatHintTooltip>
                              );
                            })}
                          </span>
                        </td>
                        <td className="td-num">{item.meetings}</td>
                        <td className={`td-num co-runners-score${scoreTone(item)}`}>
                          {scoreLabel(item)}
                        </td>
                        <td>{item.first_meeting_date ? formatDate(item.first_meeting_date) : "—"}</td>
                        <td>{item.last_meeting_date ? formatDate(item.last_meeting_date) : "—"}</td>
                      </tr>,
                      expanded ? (
                        <tr key={`${item.participant_key}-detail`} className="co-runners-detail-row">
                          <td colSpan={7}>
                            <div className="co-runners-detail-card">
                              <p className="co-runners-detail-title">
                                Встречи с участником · {item.display_name ?? "Без имени"}
                              </p>
                              {meetingsLoadingKey === item.participant_key && (
                                <p className="muted co-runners-detail-note">Загрузка встреч…</p>
                              )}
                              {meetingsError && (
                                <p className="error-text co-runners-detail-note">{meetingsError}</p>
                              )}
                              {meetingsByKey[item.participant_key] && (
                                <MeetingsDetail meetings={meetingsByKey[item.participant_key]} />
                              )}
                            </div>
                          </td>
                        </tr>
                      ) : null,
                    ];
                  })}
                </tbody>
              </table>
            </div>
            <p className="table-foot muted">
              <span>
                Показано: {filtered.length} из{" "}
                {pluralizeRu(items.length, ["участника", "участников", "участников"])}
                {items.length >= 100 ? " (топ-100 по числу встреч)" : ""}
              </span>
            </p>
          </>
        )}
      </div>
    </>
  );

  return pageBody;
}

