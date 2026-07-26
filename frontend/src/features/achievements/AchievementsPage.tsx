import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { AppShell } from "../../components/AppShell";
import { ChartColumnTooltip } from "../../components/ChartColumnTooltip";
import { PlatformBadge } from "../../components/PlatformBadge";
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
import { formatDate, platformCodeLabel, pluralizeRu } from "../../lib/format";
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

const LEVEL_MEDAL_EMOJI: Record<ChallengeLevel, string> = {
  bronze: "🥉",
  silver: "🥈",
  gold: "🥇",
};

const LEVEL_SEQUENCE: ChallengeLevel[] = ["bronze", "silver", "gold"];

const CATEGORY_TITLES: Record<Challenge["category"], string> = {
  collection: "Коллекции",
  coincidence: "Совпадения",
  scale: "Масштаб",
  community: "Вклад",
};

const CATEGORY_HINTS: Record<Challenge["category"], string> = {
  collection: "Закрывай клетки коллекций — секунды, буквы, даты и номера.",
  coincidence: "Редкие совпадения в твоих результатах.",
  scale: "Долгие челленджи на объём: локации, регионы и серии.",
  community: "Оценки и отзывы: то, что помогает другим выбрать, куда поехать.",
};

// Клетка коллекции красится в цвет системы, в которой она была закрыта раньше всего.
const PLATFORM_CELL_CLASS: Record<string, string> = {
  five_verst: "cell-plat-five-verst",
  s95: "cell-plat-s95",
  parkrun: "cell-plat-parkrun",
  runpark: "cell-plat-runpark",
};

function platformCellClass(code: string | null | undefined): string {
  if (!code) {
    return "";
  }
  return PLATFORM_CELL_CLASS[code] ?? "";
}

function PlatformColorLegend() {
  return (
    <div className="platform-color-legend">
      {(["five_verst", "s95", "parkrun", "runpark"] as const).map((code) => (
        <span key={code} className={`platform-color-legend-item ${PLATFORM_CELL_CLASS[code]}`}>
          <span className="platform-color-legend-dot" />
          {platformCodeLabel(code)}
        </span>
      ))}
    </div>
  );
}

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

function cellTooltipLines(cell: ChallengeCell): string[] {
  if (cell.done && cell.date) {
    const platform = cell.platform_code ? ` (${platformCodeLabel(cell.platform_code)})` : "";
    const lines = [`закрыто ${formatDate(cell.date)} · ${cell.location ?? ""}${platform}`.trim()];
    if (cell.count != null && cell.count > 0) {
      lines.push(pluralizeRu(cell.count, ["финиш", "финиша", "финишей"]));
    }
    return lines;
  }
  if (cell.hint) {
    return [`не закрыто. ${cell.hint}`];
  }
  return ["ещё не закрыто"];
}

function ChallengeCells({ cells }: { cells: ChallengeCell[] }) {
  const dense = cells.length > 20;
  return (
    <div className={dense ? "challenge-cells challenge-cells-dense" : "challenge-cells"}>
      {cells.map((cell) => (
        <ChartColumnTooltip key={cell.label} title={cell.label} lines={cellTooltipLines(cell)}>
          <span
            className={`challenge-cell${cell.done ? ` done ${platformCellClass(cell.platform_code)}` : ""}${
              !cell.done && cell.hint ? " has-hint" : ""
            }`}
          >
            {cell.label}
          </span>
        </ChartColumnTooltip>
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
            className={`challenge-cell${item.done ? ` done ${platformCellClass(item.platform_code)}` : " has-hint"}`}
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
                  const platformLabel = info?.platform_code ? ` (${platformCodeLabel(info.platform_code)})` : "";
                  return (
                    <span
                      key={key}
                      className={
                        info ? `challenge-year-day done ${platformCellClass(info.platform_code)}` : "challenge-year-day"
                      }
                      title={
                        info
                          ? `${label} — закрыто ${formatDate(info.date)} · ${info.location}${platformLabel}`
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

const DEJA_VU_INLINE_LIMIT = 4;

function ChallengeItems({ challenge }: { challenge: Challenge }) {
  const items = challenge.detail.items ?? [];
  const example = challenge.detail.example;
  const [expandedIndices, setExpandedIndices] = useState<Set<number>>(new Set());
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
  const toggleExpanded = (index: number) => {
    setExpandedIndices((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  };
  return (
    <ul className="challenge-items">
      {items.map((item, index) => {
        const expanded = expandedIndices.has(index);
        const occurrences = item.occurrences;
        const visibleOccurrences =
          occurrences && !expanded ? occurrences.slice(0, DEJA_VU_INLINE_LIMIT) : occurrences;
        return (
          <li key={index} className="challenge-item">
            {item.value && <span className="challenge-item-value">{item.value}</span>}
            {item.count != null && <span className="challenge-item-count">× {item.count}</span>}
            {visibleOccurrences ? (
              <span className="challenge-item-occurrences">
                {visibleOccurrences.map((occurrence, occurrenceIndex) => (
                  <span key={occurrenceIndex} className="challenge-occurrence">
                    {occurrence.location} · {formatDate(occurrence.date)}
                  </span>
                ))}
                {occurrences!.length > DEJA_VU_INLINE_LIMIT && (
                  <button
                    type="button"
                    className="challenge-occurrence-more"
                    onClick={() => toggleExpanded(index)}
                  >
                    {expanded
                      ? "свернуть"
                      : `и ещё ${occurrences!.length - DEJA_VU_INLINE_LIMIT} — показать все`}
                  </button>
                )}
              </span>
            ) : (
              item.location && <span className="challenge-item-location">{item.location}</span>
            )}
            {item.date && <span className="challenge-item-date">{formatDate(item.date)}</span>}
          </li>
        );
      })}
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
      {markers.map((marker) => {
        if (marker.at >= target) {
          return null;
        }
        const achievedDate = challenge.level_dates[marker.level];
        const title = achievedDate
          ? `${LEVEL_LABELS[marker.level]}: ${marker.at} — получено ${formatDate(achievedDate)}`
          : `${LEVEL_LABELS[marker.level]}: ${marker.at}`;
        return (
          <span
            key={marker.level}
            className={`challenge-bar-marker challenge-bar-marker-${marker.level}`}
            style={{ left: `${(marker.at / target) * 100}%` }}
            title={title}
          />
        );
      })}
    </div>
  );
}

function ChallengeLevelTimeline({ challenge }: { challenge: Challenge }) {
  return (
    <div className="challenge-level-timeline">
      {LEVEL_SEQUENCE.map((level) => {
        const achievedDate = challenge.level_dates[level];
        return (
          <span
            key={level}
            className={`challenge-level-timeline-item${achievedDate ? " done" : ""}`}
            title={`${LEVEL_LABELS[level]} (${challenge.levels[level]} ${challenge.unit ?? ""})`.trim()}
          >
            <span className="challenge-level-timeline-icon">{LEVEL_MEDAL_EMOJI[level]}</span>
            {achievedDate ? formatDate(achievedDate) : "—"}
          </span>
        );
      })}
    </div>
  );
}

// Куда вести с карточки, чтобы достижение можно было продвинуть прямо сейчас.
// Только на своей странице: на чужом профиле звать оценивать нечего.
const CHALLENGE_CTA: Record<string, { href: string; label: string }> = {
  inspector: { href: "/runs", label: "Оценить старты →" },
  reviewer: { href: "/runs", label: "Написать отзыв →" },
};

function ChallengeCard({ challenge, personal = false }: { challenge: Challenge; personal?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const cta = personal ? CHALLENGE_CTA[challenge.code] : undefined;
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
            {challenge.recent_delta > 0 && (
              <span className="recent-delta-badge" title="Продвинула последняя пробежка">
                ↑ +{challenge.recent_delta}
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
      <ChallengeLevelTimeline challenge={challenge} />
      {cta && (
        <a className="btn secondary btn-sm challenge-cta" href={cta.href}>
          {cta.label}
        </a>
      )}
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
          const achievedDate = entry.level_dates[String(threshold)];
          const title = earned
            ? achievedDate
              ? `Клуб ${threshold} — получено ${formatDate(achievedDate)}`
              : `Клуб ${threshold} — есть!`
            : isNext
              ? `Следующий клуб: ${threshold}, осталось ${entry.to_next}`
              : `Клуб ${threshold}`;
          return (
            <span
              key={threshold}
              className={`club-chip${earned ? " club-chip-earned" : ""}${isNext ? " club-chip-next" : ""}`}
              title={title}
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
            <a
              key={badge.code}
              className="achv-badge"
              href={`#challenge-${badge.code}`}
              title={badge.achieved_at ? `Получено ${formatDate(badge.achieved_at)}` : undefined}
            >
              <MedalIcon icon={badge.icon} level={badge.level} size="lg" />
              <span className="achv-badge-title">{badge.title}</span>
              <span className={`achv-badge-level achv-badge-level-${badge.level}`}>
                {LEVEL_LABELS[badge.level]}
              </span>
              {badge.achieved_at && <span className="achv-badge-date">{formatDate(badge.achieved_at)}</span>}
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

/** Витрина медалей + челленджи + клубы — без личных целей на год (те приватные,
 * задать можно только себе). Переиспользуется и на своей странице «Достижения»,
 * и на публичном профиле участника.
 *
 * platformFilter/onPlatformFilterChange — сквозной челлендж-скоуп vs скоуп
 * одной системы (владелец страницы делает рефетч /achievements?platform=…,
 * сама витрина фильтр не считает). Клубы ниже фильтром не затрагиваются —
 * они и так показывают сквозной итог и разбивку по системам одновременно. */
export function AchievementsShowcase({
  data,
  goalsSection,
  personal = false,
  platformFilter = null,
  onPlatformFilterChange,
  platformSwitching = false,
}: {
  data: AchievementsResponse;
  // «Цели на год» — личный раздел; на публичном профиле не передаётся.
  goalsSection?: ReactNode;
  // Своя страница достижений: показываем ссылки «оценить» на карточках.
  personal?: boolean;
  platformFilter?: string | null;
  onPlatformFilterChange?: (platform: string | null) => void;
  platformSwitching?: boolean;
}) {
  const grouped = useMemo(() => {
    const groups: Record<Challenge["category"], Challenge[]> = {
      collection: [],
      coincidence: [],
      scale: [],
      community: [],
    };
    for (const challenge of data.challenges) {
      groups[challenge.category]?.push(challenge);
    }
    return groups;
  }, [data]);

  const platformOptions = useMemo(
    () => data.clubs.platforms.map((platform) => platform.platform_code),
    [data.clubs.platforms],
  );

  return (
    <>
      <BadgesShowcase data={data} />

      {goalsSection}

      <section className="achv-section">
        <div className="achv-section-head">
          <h2 className="section-title">Челленджи</h2>
          <span className="muted">
            {pluralizeRu(data.summary.total, ["челлендж", "челленджа", "челленджей"])} · три уровня: бронза, серебро
            и золото
          </span>
        </div>
        {onPlatformFilterChange && platformOptions.length >= 2 && (
          <div className={`insights-filters achv-platform-filters${platformSwitching ? " is-switching" : ""}`}>
            <button
              type="button"
              className={`insights-filter-chip${platformFilter === null ? " active" : ""}`}
              onClick={() => onPlatformFilterChange(null)}
            >
              Все системы
            </button>
            {platformOptions.map((code) => (
              <button
                key={code}
                type="button"
                className={`insights-filter-chip${platformFilter === code ? " active" : ""}`}
                onClick={() => onPlatformFilterChange(code)}
              >
                {platformCodeLabel(code)}
              </button>
            ))}
          </div>
        )}
        {(Object.keys(grouped) as Array<Challenge["category"]>).map(
          (category) =>
            grouped[category].length > 0 && (
              <div key={category} className="challenge-group">
                <h3 className="challenge-group-title">{CATEGORY_TITLES[category]}</h3>
                <p className="muted challenge-group-hint">{CATEGORY_HINTS[category]}</p>
                {category === "collection" && <PlatformColorLegend />}
                <div className="challenge-grid">
                  {grouped[category].map((challenge) => (
                    <div key={challenge.code} id={`challenge-${challenge.code}`}>
                      <ChallengeCard challenge={challenge} personal={personal} />
                    </div>
                  ))}
                </div>
              </div>
            ),
        )}
      </section>

      <ClubsSection clubs={data.clubs} />
    </>
  );
}

// bare — отдать только тело страницы, без AppShell: портальный ЛК (/new/*)
// оборачивает контент в собственный каркас с сайдбаром.
function AchievementsContent({ bare = false }: { bare?: boolean } = {}) {
  const [achievements, setAchievements] = useState<AchievementsResponse | null>(null);
  const [goals, setGoals] = useState<GoalsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [platformFilter, setPlatformFilter] = useState<string | null>(null);
  const [switching, setSwitching] = useState(false);

  const load = useCallback(async (platform: string | null) => {
    setError(null);
    setSwitching(true);
    try {
      const [achievementsResponse, goalsResponse] = await Promise.all([
        getAchievements(platform ?? undefined),
        getGoals(),
      ]);
      setAchievements(achievementsResponse);
      setGoals(goalsResponse);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить данные");
    } finally {
      setSwitching(false);
    }
  }, []);

  useEffect(() => {
    void load(platformFilter);
  }, [load, platformFilter]);

  const pageBody = (
    <>
      {error && (
        <div className="card error">
          <p>{error}</p>
          <button type="button" className="btn secondary" onClick={() => void load(platformFilter)}>
            Повторить
          </button>
        </div>
      )}
      {!error && (!achievements || !goals) && <p className="muted">Загрузка…</p>}

      {achievements && goals && (
        <>
          <AchievementsShowcase
            data={achievements}
            goalsSection={<GoalsSection goals={goals} onEdit={() => setEditOpen(true)} />}
            personal
            platformFilter={platformFilter}
            onPlatformFilterChange={setPlatformFilter}
            platformSwitching={switching}
          />

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
    </>
  );

  if (bare) {
    return pageBody;
  }

  return <AppShell title="Цели и достижения">{pageBody}</AppShell>;
}

export { AchievementsContent };

