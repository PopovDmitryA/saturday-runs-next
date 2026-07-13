import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { PlatformBadge } from "../../components/PlatformBadge";
import { RequireAuth } from "../../components/RequireAuth";
import {
  getAchievements,
  getGoals,
  type AchievementsResponse,
  type Challenge,
  type ChallengeCell,
  type ChallengeLevel,
  type ClubEntry,
  type Clubs,
  type GoalsResponse,
} from "../../lib/api";
import { formatDate, pluralizeRu } from "../../lib/format";
import { GoalCard } from "./GoalCard";
import { GoalsEditModal } from "./GoalsEditModal";

const LEVEL_LABELS: Record<ChallengeLevel, string> = {
  bronze: "Бронза",
  silver: "Серебро",
  gold: "Золото",
};

const LEVEL_LABELS_TO: Record<ChallengeLevel, string> = {
  bronze: "бронзы",
  silver: "серебра",
  gold: "золота",
};

const CATEGORY_TITLES: Record<Challenge["category"], string> = {
  collection: "Коллекции",
  coincidence: "Совпадения",
  scale: "Масштаб",
};

const CATEGORY_HINTS: Record<Challenge["category"], string> = {
  collection: "Закрывай клетки коллекций — секунды, буквы, даты и номера.",
  coincidence: "Редкие совпадения в твоих результатах.",
  scale: "Долгие челленджи на объём: локации, регионы и серии.",
};

export function MedalIcon({
  icon,
  level,
  size,
}: {
  icon: string;
  level: ChallengeLevel | null;
  size?: "sm" | "lg";
}) {
  const levelClass = level ? ` medal-${level}` : " medal-locked";
  return (
    <span className={`medal${levelClass}${size === "lg" ? " medal-lg" : ""}`} aria-hidden="true">
      <span className="medal-emoji">{icon}</span>
    </span>
  );
}

function cellTitle(cell: ChallengeCell): string {
  if (cell.done && cell.date) {
    return `${cell.label} — закрыто ${formatDate(cell.date)} · ${cell.location ?? ""}`.trim();
  }
  if (cell.hint) {
    return `${cell.label} — не закрыто. ${cell.hint}`;
  }
  return `${cell.label} — ещё не закрыто`;
}

function ChallengeCells({ cells }: { cells: ChallengeCell[] }) {
  const dense = cells.length > 20;
  return (
    <div className={dense ? "challenge-cells challenge-cells-dense" : "challenge-cells"}>
      {cells.map((cell) => (
        <span
          key={cell.label}
          className={`challenge-cell${cell.done ? " done" : ""}${!cell.done && cell.hint ? " has-hint" : ""}`}
          title={cellTitle(cell)}
        >
          {cell.label}
        </span>
      ))}
    </div>
  );
}

function ChallengeLetters({ challenge }: { challenge: Challenge }) {
  const letters = challenge.detail.letters ?? [];
  return (
    <div className="challenge-cells">
      {letters.map((item) => {
        const title = item.done
          ? `Первый финиш на «${item.letter}»: ${item.location ?? ""}${item.date ? ` · ${formatDate(item.date)}` : ""}`
          : `Локации на «${item.letter}»: ${item.locations.join(", ")}${
              item.locations_more > 0 ? ` и ещё ${item.locations_more}` : ""
            }`;
        return (
          <span
            key={item.letter}
            className={`challenge-cell${item.done ? " done" : " has-hint"}`}
            title={title}
          >
            {item.letter}
          </span>
        );
      })}
    </div>
  );
}

const MONTH_SHORT = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"];
const MONTH_DAYS = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

function ChallengeDays({ challenge }: { challenge: Challenge }) {
  const days = challenge.detail.days ?? [];
  const byKey = useMemo(() => new Map(days.map((day) => [day.key, day])), [days]);
  return (
    <div className="challenge-year-scroll">
      <div className="challenge-year">
        {MONTH_SHORT.map((monthLabel, monthIndex) => {
          const daysInMonth = MONTH_DAYS[monthIndex];
          const closed = days.filter(
            (day) => Number(day.key.slice(0, 2)) === monthIndex + 1,
          ).length;
          return (
            <div key={monthLabel} className="challenge-year-month">
              <span className="challenge-year-month-label">{monthLabel}</span>
              <span className="challenge-year-month-count">
                {closed}/{daysInMonth}
              </span>
              <div className="challenge-year-days">
                {Array.from({ length: daysInMonth }, (_, dayIndex) => {
                  const key = `${String(monthIndex + 1).padStart(2, "0")}-${String(dayIndex + 1).padStart(2, "0")}`;
                  const info = byKey.get(key);
                  const label = `${String(dayIndex + 1).padStart(2, "0")}.${String(monthIndex + 1).padStart(2, "0")}`;
                  return (
                    <span
                      key={key}
                      className={info ? "challenge-year-day done" : "challenge-year-day"}
                      title={
                        info
                          ? `${label} — закрыто ${formatDate(info.date)} · ${info.location}`
                          : `${label} — ещё не закрыто`
                      }
                    />
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ChallengeItems({ challenge }: { challenge: Challenge }) {
  const items = challenge.detail.items ?? [];
  const example = challenge.detail.example;
  if (items.length === 0) {
    return (
      <div className="challenge-items-empty">
        <p className="muted challenge-empty">Пока ни одного — всё впереди!</p>
        {example && (
          <div className="challenge-item challenge-item-example">
            <span className="challenge-item-value">{example.value}</span>
            <span className="challenge-item-location">{example.location}</span>
            <span className="challenge-item-date">сегодня</span>
            <span className="challenge-item-example-note muted">Пример: {example.note}</span>
          </div>
        )}
      </div>
    );
  }
  return (
    <ul className="challenge-items">
      {items.map((item, index) => (
        <li key={index} className="challenge-item">
          {item.value && <span className="challenge-item-value">{item.value}</span>}
          {item.count != null && <span className="challenge-item-count">× {item.count}</span>}
          {item.occurrences ? (
            <span className="challenge-item-occurrences">
              {item.occurrences.map((occurrence, occurrenceIndex) => (
                <span key={occurrenceIndex} className="challenge-occurrence">
                  {occurrence.location} · {formatDate(occurrence.date)}
                </span>
              ))}
              {item.count != null && item.count > item.occurrences.length && (
                <span className="challenge-occurrence-more">
                  и ещё {item.count - item.occurrences.length}
                </span>
              )}
            </span>
          ) : (
            item.location && <span className="challenge-item-location">{item.location}</span>
          )}
          {item.date && <span className="challenge-item-date">{formatDate(item.date)}</span>}
        </li>
      ))}
    </ul>
  );
}

function ChallengeDetailBlock({ challenge }: { challenge: Challenge }) {
  const detail = challenge.detail;
  if (detail.letters) {
    return <ChallengeLetters challenge={challenge} />;
  }
  if (detail.cells) {
    return <ChallengeCells cells={detail.cells} />;
  }
  if (detail.days) {
    return <ChallengeDays challenge={challenge} />;
  }
  return <ChallengeItems challenge={challenge} />;
}

function hasDetail(challenge: Challenge): boolean {
  const detail = challenge.detail;
  return Boolean(
    detail.cells?.length || detail.letters?.length || detail.days || detail.items || detail.example,
  );
}

function ChallengeProgressBar({ challenge }: { challenge: Challenge }) {
  const { levels, target } = challenge;
  const markers: Array<{ level: ChallengeLevel; at: number }> = [
    { level: "bronze", at: levels.bronze },
    { level: "silver", at: levels.silver },
  ];
  return (
    <div className="challenge-bar" role="img" aria-label={`Прогресс ${challenge.pct}%`}>
      <div
        className={`challenge-bar-fill${challenge.level ? ` challenge-bar-${challenge.level}` : ""}`}
        style={{ width: `${challenge.pct}%` }}
      />
      {markers.map(
        (marker) =>
          marker.at < target && (
            <span
              key={marker.level}
              className={`challenge-bar-marker challenge-bar-marker-${marker.level}`}
              style={{ left: `${(marker.at / target) * 100}%` }}
              title={`${LEVEL_LABELS[marker.level]}: ${marker.at}`}
            />
          ),
      )}
    </div>
  );
}

function ChallengeCard({ challenge }: { challenge: Challenge }) {
  const [expanded, setExpanded] = useState(false);
  const nextHint = challenge.next_level
    ? `До ${LEVEL_LABELS_TO[challenge.next_level]}: ${
        challenge.to_next_label ?? `ещё ${challenge.to_next_level ?? "—"}`
      }`
    : "Максимальный уровень!";
  return (
    <div className={`card challenge-card${challenge.level ? "" : " challenge-card-locked"}`}>
      <div className="challenge-head">
        <MedalIcon icon={challenge.icon} level={challenge.level} />
        <div className="challenge-head-text">
          <div className="challenge-title-row">
            <h3 className="challenge-title">{challenge.title}</h3>
            {challenge.level && (
              <span className={`challenge-level-chip challenge-level-${challenge.level}`}>
                {LEVEL_LABELS[challenge.level]}
              </span>
            )}
          </div>
          <p className="challenge-description">{challenge.description}</p>
        </div>
      </div>
      <ChallengeProgressBar challenge={challenge} />
      <div className="challenge-meta">
        <span className="challenge-progress-label">
          <strong>{challenge.current}</strong> / {challenge.target}
          {challenge.unit ? ` ${challenge.unit}` : ""}
        </span>
        <span className="challenge-next muted">{nextHint}</span>
      </div>
      {hasDetail(challenge) && (
        <>
          {expanded && <ChallengeDetailBlock challenge={challenge} />}
          <button
            type="button"
            className="challenge-toggle"
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? "Скрыть детали ↑" : "Детали ↓"}
          </button>
        </>
      )}
    </div>
  );
}

function ClubRow({ entry }: { entry: ClubEntry }) {
  return (
    <div className="club-row">
      <div className="club-row-head">
        <span className="club-row-icon" aria-hidden="true">
          {entry.icon}
        </span>
        <span className="club-row-title">{entry.title}</span>
        <span className="club-row-current">{entry.current}</span>
      </div>
      <div className="club-chips">
        {entry.thresholds.map((threshold) => {
          const earned = entry.earned.includes(threshold);
          const isNext = entry.next_threshold === threshold;
          return (
            <span
              key={threshold}
              className={`club-chip${earned ? " club-chip-earned" : ""}${isNext ? " club-chip-next" : ""}`}
              title={
                earned
                  ? `Клуб ${threshold} — есть!`
                  : isNext
                    ? `Следующий клуб: ${threshold}, осталось ${entry.to_next}`
                    : `Клуб ${threshold}`
              }
            >
              {threshold}
            </span>
          );
        })}
      </div>
      {entry.next_threshold != null ? (
        <div className="club-progress">
          <div className="club-bar">
            <div className="club-bar-fill" style={{ width: `${entry.pct_to_next}%` }} />
          </div>
          <span className="club-progress-label muted">
            до клуба {entry.next_threshold}: ещё {entry.to_next}
          </span>
        </div>
      ) : (
        <span className="club-progress-label muted">Все клубы собраны 🏆</span>
      )}
    </div>
  );
}

function ClubsSection({ clubs }: { clubs: Clubs }) {
  return (
    <section className="achv-section">
      <div className="achv-section-head">
        <h2 className="section-title">Клубы</h2>
        <span className="muted">ступени 10 / 25 / 50 / 100 / 250 / 500 / 1000</span>
      </div>
      <div className="challenge-group">
        <h3 className="challenge-group-title">По всем системам</h3>
        <p className="muted challenge-group-hint">
          Сквозной зачёт: пробежки и волонтёрства во всех системах вместе.
        </p>
        <div className="club-grid">
          {clubs.overall.map((entry) => (
            <div key={entry.code} className="card club-card">
              <ClubRow entry={entry} />
            </div>
          ))}
        </div>
      </div>
      {clubs.platforms.length > 0 && (
        <div className="challenge-group">
          <h3 className="challenge-group-title">По системам</h3>
          <p className="muted challenge-group-hint">Те же ступени внутри каждой системы отдельно.</p>
          <div className="club-grid">
            {clubs.platforms.map((platform) => (
              <div key={platform.platform_code} className="card club-card">
                <div className="club-card-platform">
                  <PlatformBadge code={platform.platform_code} />
                </div>
                {platform.entries.map((entry) => (
                  <ClubRow key={entry.code} entry={entry} />
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function BadgesShowcase({ data }: { data: AchievementsResponse }) {
  const { badges, summary } = data;
  return (
    <div className="card achv-showcase">
      <div className="achv-showcase-head">
        <h2 className="section-title">Мои достижения</h2>
        <span className="achv-summary">
          {summary.gold > 0 && <span className="achv-summary-item achv-summary-gold">🥇 {summary.gold}</span>}
          {summary.silver > 0 && <span className="achv-summary-item achv-summary-silver">🥈 {summary.silver}</span>}
          {summary.bronze > 0 && <span className="achv-summary-item achv-summary-bronze">🥉 {summary.bronze}</span>}
          <span className="muted">
            {badges.length} из {summary.total}
          </span>
        </span>
      </div>
      {badges.length === 0 ? (
        <p className="muted">
          Пока нет медалей — но первые уже близко. Загляни в челленджи ниже: у некоторых бронза начинается с одного
          совпадения.
        </p>
      ) : (
        <div className="achv-badges">
          {badges.map((badge) => (
            <a key={badge.code} className="achv-badge" href={`#challenge-${badge.code}`}>
              <MedalIcon icon={badge.icon} level={badge.level} size="lg" />
              <span className="achv-badge-title">{badge.title}</span>
              <span className={`achv-badge-level achv-badge-level-${badge.level}`}>
                {LEVEL_LABELS[badge.level]}
              </span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

function GoalsSection({
  goals,
  onEdit,
}: {
  goals: GoalsResponse;
  onEdit: () => void;
}) {
  return (
    <section className="achv-section">
      <div className="achv-section-head">
        <h2 className="section-title">Цели на {goals.year} год</h2>
        <button type="button" className="btn secondary btn-sm" onClick={onEdit}>
          {goals.goals.length > 0 ? "Настроить цели" : "Выбрать цели"}
        </button>
      </div>
      {goals.goals.length === 0 ? (
        <div className="card achv-goals-empty">
          <p>
            Поставь себе до {goals.max_goals} целей на год — пробежки, волонтёрства, новые локации или время на
            дистанции. Прогресс будет виден на главной, а мы подскажем, успеваешь ли ты к 31 декабря.
          </p>
          <button type="button" className="btn" onClick={onEdit}>
            Выбрать цели →
          </button>
        </div>
      ) : (
        <div className="goal-grid">
          {goals.goals.map((goal) => (
            <GoalCard key={goal.goal_type} goal={goal} />
          ))}
        </div>
      )}
    </section>
  );
}

function AchievementsContent() {
  const [achievements, setAchievements] = useState<AchievementsResponse | null>(null);
  const [goals, setGoals] = useState<GoalsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [achievementsResponse, goalsResponse] = await Promise.all([getAchievements(), getGoals()]);
      setAchievements(achievementsResponse);
      setGoals(goalsResponse);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить данные");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const grouped = useMemo(() => {
    const groups: Record<Challenge["category"], Challenge[]> = {
      collection: [],
      coincidence: [],
      scale: [],
    };
    for (const challenge of achievements?.challenges ?? []) {
      groups[challenge.category]?.push(challenge);
    }
    return groups;
  }, [achievements]);

  return (
    <AppShell title="Цели и достижения">
      {error && (
        <div className="card error">
          <p>{error}</p>
          <button type="button" className="btn secondary" onClick={() => void load()}>
            Повторить
          </button>
        </div>
      )}
      {!error && (!achievements || !goals) && <p className="muted">Загрузка…</p>}

      {achievements && goals && (
        <>
          <BadgesShowcase data={achievements} />

          <GoalsSection goals={goals} onEdit={() => setEditOpen(true)} />

          <section className="achv-section">
            <div className="achv-section-head">
              <h2 className="section-title">Челленджи</h2>
              <span className="muted">
                {pluralizeRu(achievements.summary.total, ["челлендж", "челленджа", "челленджей"])} · три уровня:
                бронза, серебро и золото
              </span>
            </div>
            {(Object.keys(grouped) as Array<Challenge["category"]>).map(
              (category) =>
                grouped[category].length > 0 && (
                  <div key={category} className="challenge-group">
                    <h3 className="challenge-group-title">{CATEGORY_TITLES[category]}</h3>
                    <p className="muted challenge-group-hint">{CATEGORY_HINTS[category]}</p>
                    <div className="challenge-grid">
                      {grouped[category].map((challenge) => (
                        <div key={challenge.code} id={`challenge-${challenge.code}`}>
                          <ChallengeCard challenge={challenge} />
                        </div>
                      ))}
                    </div>
                  </div>
                ),
            )}
          </section>

          <ClubsSection clubs={achievements.clubs} />

          <GoalsEditModal
            open={editOpen}
            goals={goals}
            onClose={() => setEditOpen(false)}
            onSaved={(updated) => {
              setGoals(updated);
              setEditOpen(false);
            }}
          />
        </>
      )}
    </AppShell>
  );
}

export function AchievementsPage() {
  return <RequireAuth>{() => <AchievementsContent />}</RequireAuth>;
}
