import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AppShell } from "../../components/AppShell";
import { ChartColumnTooltip } from "../../components/ChartColumnTooltip";
import { TitleTooltipZone } from "../../components/TitleTooltipZone";
import { PlatformBadge } from "../../components/PlatformBadge";
import {
  getAchievements,
  getGoals,
  type AchievementsResponse,
  type Challenge,
  type ChallengeCell,
  type ChallengeLevel,
  type ChallengeTier,
  type ClubEntry,
  type Clubs,
  type GoalsResponse,
} from "../../lib/api";
import { formatDate, parseIsoDate, platformCodeLabel, pluralizeRu } from "../../lib/format";
import { GoalCard } from "./GoalCard";
import { GoalsEditModal } from "./GoalsEditModal";
import { StartNumbersPlanModal } from "./StartNumbersPlanModal";

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

const TIER_LABELS: Record<string, string> = {
  easy: "лёгкий",
  medium: "средний",
  hard: "сложный",
};

// Порядок от простого к сложному — на нём строятся вкладки и выбор «текущего» тира.
const TIER_SEQUENCE = ["easy", "medium", "hard"] as const;

// Полосы, залипающие сверху и перекрывающие цель прокрутки: шапка портала и
// фильтр систем над сеткой челленджей.
const STICKY_TOP_SELECTORS = [".portal-header", ".achv-platform-filters"];
// Зазор между низом залипших полос и верхом карточки.
const JUMP_BREATHING_PX = 12;

/** Суммарная высота реально залипших сверху полос. Считаем по факту: на
 *  публичном профиле фильтра систем нет, а его высота зависит от числа систем
 *  (на узком экране чипы переносятся на вторую строку). */
function stickyOffsetTop(): number {
  let offset = 0;
  for (const selector of STICKY_TOP_SELECTORS) {
    const node = document.querySelector<HTMLElement>(selector);
    if (!node) {
      continue;
    }
    const position = window.getComputedStyle(node).position;
    if (position === "sticky" || position === "fixed") {
      offset += node.getBoundingClientRect().height;
    }
  }
  return offset + JUMP_BREATHING_PX;
}

/** Тир челленджа по ключу. «Семь дней» тиров не имеет (единственный "solo"), но
 *  показывать его надо на любой вкладке — иначе он просто исчезнет из витрины. */
function tierByKey(challenge: Challenge, tierKey: string): ChallengeTier | undefined {
  return (
    challenge.tiers.find((tier) => tier.tier === tierKey) ??
    challenge.tiers.find((tier) => tier.tier === "solo")
  );
}

/** Вкладка витрины по умолчанию — тир, на котором пользователь сейчас чаще всего
 *  находится (по best_tier). При равенстве выбираем более лёгкий. */
function defaultShowcaseTier(challenges: Challenge[]): string {
  const counts = new Map<string, number>();
  for (const challenge of challenges) {
    if (challenge.best_tier && challenge.best_tier !== "solo") {
      counts.set(challenge.best_tier, (counts.get(challenge.best_tier) ?? 0) + 1);
    }
  }
  let best: string = TIER_SEQUENCE[0];
  let bestCount = -1;
  for (const tier of TIER_SEQUENCE) {
    const count = counts.get(tier) ?? 0;
    if (count > bestCount) {
      best = tier;
      bestCount = count;
    }
  }
  return best;
}

// Тир кодируется цветом (точка на вкладке, кольцо вокруг иконки) — слова
// «лёгкий/средний/сложный» рядом с «Бронза/Серебро/Золото» сливались в одно
// капслочное месиво. Класс есть только у настоящих тиров; "solo" даёт "".
const TIER_DOT_CLASS: Record<string, string> = {
  easy: "tier-dot-easy",
  medium: "tier-dot-medium",
  hard: "tier-dot-hard",
};

const TIER_RING_CLASS: Record<string, string> = {
  easy: "medal-ring-easy",
  medium: "medal-ring-medium",
  hard: "medal-ring-hard",
};

// Кольцо тира вокруг медали достижения. Без тира (нет медали или solo-челлендж)
// просто отдаёт детей как есть.
function TierRing({ tier, title, children }: { tier: string | null; title?: string; children: ReactNode }) {
  const ringClass = tier ? TIER_RING_CLASS[tier] : undefined;
  if (!ringClass) {
    return <>{children}</>;
  }
  return (
    <span className={`medal-tier-ring ${ringClass}`} title={title}>
      {children}
    </span>
  );
}

function TierLegend() {
  return (
    <div className="tier-legend" aria-hidden="true">
      <span className="tier-legend-caption">Уровни сложности:</span>
      {TIER_SEQUENCE.map((tier) => (
        <span key={tier} className="tier-legend-item">
          <span className={`tier-tab-dot ${TIER_DOT_CLASS[tier]}`} />
          {TIER_LABELS[tier]}
        </span>
      ))}
    </div>
  );
}

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

/**
 * Клетка закрыта тем самым последним днём активности, за который на карточке
 * стоит «↑ +1». Раньше эту клетку приходилось искать глазами, вспоминая, каким
 * по счёту был старт последней недели, — теперь она подсвечена в «Деталях».
 */
function isFreshlyClosed(date: string | null | undefined, recentDate: string | null): boolean {
  return Boolean(recentDate && date && date === recentDate);
}

/** Подпись в подсказке у клетки, закрытой последней пробежкой. */
const FRESH_TOOLTIP_LINE = "🆕 закрыто последней пробежкой";

function cellTooltipLines(cell: ChallengeCell, recentDate: string | null): string[] {
  if (cell.done && cell.date) {
    const platform = cell.platform_code ? ` (${platformCodeLabel(cell.platform_code)})` : "";
    const lines = [`закрыто ${formatDate(cell.date)} · ${cell.location ?? ""}${platform}`.trim()];
    if (cell.count != null && cell.count > 0) {
      lines.push(pluralizeRu(cell.count, ["финиш", "финиша", "финишей"]));
    }
    if (isFreshlyClosed(cell.date, recentDate)) {
      lines.push(FRESH_TOOLTIP_LINE);
    }
    return lines;
  }
  if (cell.hint) {
    return [`не закрыто. ${cell.hint}`];
  }
  return ["ещё не закрыто"];
}

function ChallengeCells({
  cells,
  recentDate,
}: {
  cells: ChallengeCell[];
  recentDate: string | null;
}) {
  const dense = cells.length > 20;
  return (
    <div className={dense ? "challenge-cells challenge-cells-dense" : "challenge-cells"}>
      {cells.map((cell) => (
        <ChartColumnTooltip
          key={cell.label}
          title={cell.label}
          lines={cellTooltipLines(cell, recentDate)}
        >
          <span
            className={`challenge-cell${cell.done ? ` done ${platformCellClass(cell.platform_code)}` : ""}${
              !cell.done && cell.hint ? " has-hint" : ""
            }${isFreshlyClosed(cell.date, recentDate) ? " challenge-cell-fresh" : ""}`}
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
  const recentDate = challenge.recent_date;
  return (
    // Тёмная плашка вместо нативной подсказки браузера — как в «Коллекциях».
    <TitleTooltipZone className="challenge-cells">
      {letters.map((item) => {
        const fresh = isFreshlyClosed(item.date, recentDate);
        const title = item.done
          ? `Первый финиш на «${item.letter}»: ${item.location ?? ""}${item.date ? ` · ${formatDate(item.date)}` : ""}${
              fresh ? `\n${FRESH_TOOLTIP_LINE}` : ""
            }`
          : `Локации на «${item.letter}»: ${item.locations.join(", ")}${
              item.locations_more > 0 ? ` и ещё ${item.locations_more}` : ""
            }`;
        return (
          <span
            key={item.letter}
            className={`challenge-cell${item.done ? ` done ${platformCellClass(item.platform_code)}` : " has-hint"}${
              fresh ? " challenge-cell-fresh" : ""
            }`}
            title={title}
          >
            {item.letter}
          </span>
        );
      })}
    </TitleTooltipZone>
  );
}

const MONTH_SHORT = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"];
const MONTH_DAYS = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

function ChallengeDays({ challenge }: { challenge: Challenge }) {
  const days = challenge.detail.days ?? [];
  const byKey = useMemo(() => new Map(days.map((day) => [day.key, day])), [days]);
  return (
    <TitleTooltipZone className="challenge-year-scroll">
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
                  const fresh = isFreshlyClosed(info?.date, challenge.recent_date);
                  return (
                    <span
                      key={key}
                      className={`${
                        info ? `challenge-year-day done ${platformCellClass(info.platform_code)}` : "challenge-year-day"
                      }${fresh ? " challenge-year-day-fresh" : ""}`}
                      title={
                        info
                          ? `${label} — закрыто ${formatDate(info.date)} · ${info.location}${platformLabel}${
                              fresh ? `\n${FRESH_TOOLTIP_LINE}` : ""
                            }`
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
    </TitleTooltipZone>
  );
}

const DEJA_VU_INLINE_LIMIT = 4;

function ChallengeItems({ challenge }: { challenge: Challenge }) {
  const items = challenge.detail.items ?? [];
  const example = challenge.detail.example;
  const recentDate = challenge.recent_date;
  // Свежая запись, спрятанная под «показать все», — это ровно то, что человек
  // и ищет в деталях, поэтому такие строки раскрываем сразу.
  const [expandedIndices, setExpandedIndices] = useState<Set<number>>(
    () =>
      new Set(
        items.flatMap((item, index) =>
          (item.occurrences ?? []).some((occurrence) =>
            isFreshlyClosed(occurrence.date, recentDate),
          )
            ? [index]
            : [],
        ),
      ),
  );
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
        const fresh =
          isFreshlyClosed(item.date, recentDate) ||
          (occurrences ?? []).some((occurrence) => isFreshlyClosed(occurrence.date, recentDate));
        return (
          <li
            key={index}
            className={`challenge-item${fresh ? " challenge-item-fresh" : ""}`}
            title={fresh ? FRESH_TOOLTIP_LINE : undefined}
          >
            {item.value && <span className="challenge-item-value">{item.value}</span>}
            {item.count != null && <span className="challenge-item-count">× {item.count}</span>}
            {visibleOccurrences ? (
              <span className="challenge-item-occurrences">
                {visibleOccurrences.map((occurrence, occurrenceIndex) => (
                  <span
                    key={occurrenceIndex}
                    className={`challenge-occurrence${
                      isFreshlyClosed(occurrence.date, recentDate) ? " challenge-occurrence-fresh" : ""
                    }`}
                  >
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
    return <ChallengeCells cells={detail.cells} recentDate={challenge.recent_date} />;
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

/** Короткая дата для подписи на баре: «18.05.24» вместо «18.05.2024». */
function shortDate(value: string): string {
  const parsed = parseIsoDate(value);
  if (!parsed) {
    return formatDate(value);
  }
  const dd = String(parsed.getDate()).padStart(2, "0");
  const mm = String(parsed.getMonth() + 1).padStart(2, "0");
  return `${dd}.${mm}.${String(parsed.getFullYear()).slice(2)}`;
}

/** Разводит подписи, если пороги стоят слишком близко (напр. 45/50/55 при
 *  золоте 55 — это 82%/91%/100% шкалы, подписи бы наехали друг на друга).
 *  Сами риски остаются на честных позициях, сдвигаются ТОЛЬКО подписи.
 *  halfLabel — половина ширины подписи в % шкалы: последняя подпись прижата к
 *  правому краю, поэтому её реальный центр левее 100%, и предыдущую надо
 *  разводить относительно этого центра, а не относительно самих 100%. */
function spreadLabelPositions(positions: number[], minGap: number, halfLabel: number): number[] {
  const spread = [...positions];
  const last = spread.length - 1;
  for (let i = 1; i <= last; i += 1) {
    spread[i] = Math.max(spread[i], spread[i - 1] + minGap);
  }
  // Обратный проход: держим цепочку внутри шкалы, сохраняя зазоры.
  spread[last] = Math.min(spread[last], 100);
  let cap = Math.min(spread[last], 100 - halfLabel);
  for (let i = last - 1; i >= 0; i -= 1) {
    spread[i] = Math.min(spread[i], cap - minGap);
    cap = spread[i];
  }
  return spread.map((value) => Math.max(value, 0));
}

// Минимальный зазор между подписями в % шкалы. Подпись двухстрочная («🥉 40»
// над «18.05.24»), самая широкая строка — дата, ~55px при ширине карточки от
// 330px, отсюда ~20%.
const LABEL_MIN_GAP_PCT = 20;
// Крайние подписи центрировать нельзя — половина уезжает за край карточки,
// поэтому у краёв переключаемся на выравнивание по краю.
const LABEL_EDGE_PCT = 12;
// Половина ширины подписи в % шкалы — на сколько прижатая к краю подпись
// «заходит» внутрь шкалы (см. spreadLabelPositions).
const LABEL_HALF_PCT = 9;

function ChallengeProgressBar({
  tier,
  current,
  unit,
}: {
  tier: ChallengeTier;
  current: number;
  unit: string | null;
}) {
  const { levels, target } = tier;
  const marks = LEVEL_SEQUENCE.map((level) => ({
    level,
    at: levels[level],
    date: tier.level_dates[level],
    pct: target ? Math.min((levels[level] / target) * 100, 100) : 0,
  }));
  const labelPositions = spreadLabelPositions(
    marks.map((mark) => mark.pct),
    LABEL_MIN_GAP_PCT,
    LABEL_HALF_PCT,
  );
  return (
    <div className="challenge-bar-wrap">
      <div className="challenge-bar" role="img" aria-label={`Прогресс ${tier.pct}%`}>
        <div
          className={`challenge-bar-fill${tier.level ? ` challenge-bar-${tier.level}` : ""}`}
          style={{ width: `${tier.pct}%` }}
        />
        {marks.map((mark) =>
          mark.pct >= 100 ? null : (
            <span
              key={mark.level}
              className={`challenge-bar-marker challenge-bar-marker-${mark.level}`}
              style={{ left: `${mark.pct}%` }}
            />
          ),
        )}
      </div>
      {marks.map((mark, index) => {
        const done = Boolean(mark.date);
        // Дата — для взятых уровней, «ещё N» — для оставшихся: подпись на баре
        // должна отвечать «сколько надо» и «когда взял» без наведения мыши.
        const detail = mark.date ? shortDate(mark.date) : `ещё ${Math.max(mark.at - current, 0)}`;
        const pos = labelPositions[index];
        const atStart = pos <= LABEL_EDGE_PCT;
        const atEnd = pos >= 100 - LABEL_EDGE_PCT;
        const edgeClass = atStart ? " at-start" : atEnd ? " at-end" : "";
        const style = atStart ? { left: 0 } : atEnd ? { right: 0 } : { left: `${pos}%` };
        return (
          <span
            key={mark.level}
            className={`challenge-bar-label${done ? " done" : ""}${edgeClass}`}
            style={style}
            title={`${LEVEL_LABELS[mark.level]}: ${mark.at} ${unit ?? ""}`.trim()}
          >
            <span className="challenge-bar-label-top">
              <span className="challenge-bar-label-medal">{LEVEL_MEDAL_EMOJI[mark.level]}</span>
              <span className="challenge-bar-label-threshold">{mark.at}</span>
            </span>
            <span className="challenge-bar-label-detail">{detail}</span>
          </span>
        );
      })}
    </div>
  );
}

// Вкладки уровней сложности над прогресс-баром. Скрываются сами, если у
// челленджа один тир (сегодня — только «Семь дней», solo без вкладок).
function TierTabs({
  tiers,
  active,
  onChange,
}: {
  tiers: ChallengeTier[];
  active: string;
  onChange: (tier: string) => void;
}) {
  if (tiers.length <= 1) {
    return null;
  }
  return (
    <div className="tier-tabs" role="tablist">
      {tiers.map((tier) => {
        const label = (tier.label ?? TIER_LABELS[tier.tier] ?? tier.tier).toLowerCase();
        return (
          <button
            key={tier.tier}
            type="button"
            role="tab"
            aria-selected={tier.tier === active}
            className={`tier-tab${tier.tier === active ? " active" : ""}`}
            onClick={() => onChange(tier.tier)}
            title={`Уровень сложности: ${label}`}
          >
            <span className={`tier-tab-dot ${TIER_DOT_CLASS[tier.tier] ?? ""}`} />
            {label}
            <span className={`tier-tab-medal${tier.level ? "" : " empty"}`}>
              {tier.level ? LEVEL_MEDAL_EMOJI[tier.level] : "🥉"}
            </span>
          </button>
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

// Челленджи, где есть что планировать наперёд: таблица «номер старта → у каких
// локаций он выпадает на ближайшие три недели». Тултипы ячеек показывают то же
// самое, но по одной ячейке за раз — для долгого планирования это неудобно.
const CHALLENGE_PLAN_CODES = new Set(["start_numbers", "start_numbers_pro"]);

function ChallengeCard({
  challenge,
  personal = false,
  platformFilter = null,
}: {
  challenge: Challenge;
  personal?: boolean;
  // Фильтр систем со страницы достижений: планирование обязано показывать те же
  // системы, что и сама карточка, иначе под фильтром «5 вёрст» в таблице
  // всплывали бы старты s95 и runpark.
  platformFilter?: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const [planOpen, setPlanOpen] = useState(false);
  const [activeTierKey, setActiveTierKey] = useState(challenge.default_tier);
  // Пришли новые цифры (сменился фильтр систем) — вкладка сложности встаёт на
  // актуальный тир. Именно так, точечно, а не перемонтированием карточки: иначе
  // вместе с вкладкой схлопывались раскрытые «Детали».
  const dataKey = `${challenge.current}-${challenge.default_tier}`;
  const prevDataKey = useRef(dataKey);
  useEffect(() => {
    if (prevDataKey.current !== dataKey) {
      prevDataKey.current = dataKey;
      setActiveTierKey(challenge.default_tier);
    }
  }, [dataKey, challenge.default_tier]);
  const activeTier = challenge.tiers.find((tier) => tier.tier === activeTierKey) ?? challenge.tiers[0];
  const bestTier = challenge.best_tier
    ? challenge.tiers.find((tier) => tier.tier === challenge.best_tier)
    : undefined;
  const cta = personal ? CHALLENGE_CTA[challenge.code] : undefined;
  const hasPlan = personal && CHALLENGE_PLAN_CODES.has(challenge.code);
  const nextHint = activeTier.next_level
    ? `До ${LEVEL_LABELS_TO[activeTier.next_level]}: ${
        activeTier.to_next_label ?? `ещё ${activeTier.to_next_level ?? "—"}`
      }`
    : "Уровень пройден целиком!";
  const bestTierLabel = bestTier?.label ? bestTier.label.toLowerCase() : null;
  return (
    <div className={`card challenge-card${challenge.best_level ? "" : " challenge-card-locked"}`}>
      <div className="challenge-head">
        <TierRing
          tier={challenge.best_level ? challenge.best_tier : null}
          title={bestTierLabel ? `Медаль взята на уровне «${bestTierLabel}»` : undefined}
        >
          <MedalIcon icon={challenge.icon} level={challenge.best_level} />
        </TierRing>
        <div className="challenge-head-text">
          <div className="challenge-title-row">
            <h3 className="challenge-title">{challenge.title}</h3>
            {challenge.best_level && (
              <span
                className={`challenge-level-chip challenge-level-${challenge.best_level}`}
                title={bestTierLabel ? `Уровень сложности: ${bestTierLabel}` : undefined}
              >
                {LEVEL_LABELS[challenge.best_level]}
              </span>
            )}
            {challenge.recent_delta > 0 && (
              <span
                className="recent-delta-badge"
                title={
                  challenge.recent_date
                    ? `Продвинула пробежка ${formatDate(challenge.recent_date)} — в «Деталях» она подсвечена`
                    : "Продвинула последняя пробежка"
                }
              >
                ↑ +{challenge.recent_delta}
              </span>
            )}
          </div>
          <p className="challenge-description">{challenge.description}</p>
        </div>
      </div>
      <TierTabs tiers={challenge.tiers} active={activeTier.tier} onChange={setActiveTierKey} />
      <ChallengeProgressBar tier={activeTier} current={challenge.current} unit={challenge.unit} />
      <div className="challenge-meta">
        <span className="challenge-progress-label">
          <strong>{challenge.current}</strong> / {activeTier.target}
          {challenge.unit ? ` ${challenge.unit}` : ""}
        </span>
        <span className="challenge-next muted">{nextHint}</span>
      </div>
      {cta && (
        <a className="btn secondary btn-sm challenge-cta" href={cta.href}>
          {cta.label}
        </a>
      )}
      {hasPlan && (
        <>
          <button
            type="button"
            className="challenge-plan-link"
            onClick={() => setPlanOpen(true)}
          >
            Планирование →
          </button>
          <StartNumbersPlanModal
            open={planOpen}
            code={challenge.code}
            challengeTitle={challenge.title}
            platform={platformFilter}
            onClose={() => setPlanOpen(false)}
          />
        </>
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

function BadgesShowcase({
  data,
  onJump,
  recalculating = false,
}: {
  data: AchievementsResponse;
  onJump: (code: string) => void;
  // Идёт перезапрос под новый фильтр систем — приглушаем блок на время пересчёта.
  recalculating?: boolean;
}) {
  // Сквозной счёт «золото/серебро/бронза» по всем челленджам смысла не имеет:
  // золото лёгкого тира и золото сложного — разные вещи, в одной сумме они
  // смешивались. Поэтому витрина показывает срез ОДНОГО уровня сложности.
  const defaultTier = defaultShowcaseTier(data.challenges);
  const [tierTab, setTierTab] = useState(defaultTier);
  // Сменился скоуп данных — вкладка встаёт на актуальный тир (без ремоунта,
  // чтобы витрина не мигала).
  const prevDefaultTier = useRef(defaultTier);
  useEffect(() => {
    if (prevDefaultTier.current !== defaultTier) {
      prevDefaultTier.current = defaultTier;
      setTierTab(defaultTier);
    }
  }, [defaultTier]);

  // Смена уровня меняет и счётчик, и весь набор медалей (их число тоже разное),
  // поэтому контент не подменяем мгновенно: гасим, переключаем, проявляем — как
  // при смене фильтра систем. Перекомпоновка происходит в погашенном состоянии.
  const [fading, setFading] = useState(false);
  const fadeTimer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(fadeTimer.current), []);

  const changeTierTab = (tier: string) => {
    if (tier === tierTab) {
      return;
    }
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setTierTab(tier);
      return;
    }
    setFading(true);
    window.clearTimeout(fadeTimer.current);
    fadeTimer.current = window.setTimeout(() => {
      setTierTab(tier);
      setFading(false);
    }, 180);
  };

  const dimmed = fading || recalculating;

  const view = useMemo(() => {
    const rows: Array<{
      code: string;
      title: string;
      icon: string;
      level: ChallengeLevel;
      tier: string;
      achievedAt: string | null;
    }> = [];
    let inTier = 0;
    for (const challenge of data.challenges) {
      const tier = tierByKey(challenge, tierTab);
      if (!tier) {
        continue;
      }
      inTier += 1;
      if (tier.level) {
        rows.push({
          code: challenge.code,
          title: challenge.title,
          icon: challenge.icon,
          level: tier.level,
          tier: tier.tier,
          achievedAt: tier.level_dates[tier.level],
        });
      }
    }
    rows.sort((a, b) => LEVEL_SEQUENCE.indexOf(b.level) - LEVEL_SEQUENCE.indexOf(a.level));
    return {
      rows,
      inTier,
      gold: rows.filter((row) => row.level === "gold").length,
      silver: rows.filter((row) => row.level === "silver").length,
      bronze: rows.filter((row) => row.level === "bronze").length,
    };
  }, [data.challenges, tierTab]);

  return (
    <div className="card achv-showcase">
      <div className="achv-showcase-head">
        <h2 className="section-title">Мои достижения</h2>
        <div className="achv-showcase-head-right">
          <div className="tier-tabs achv-showcase-tabs" role="tablist">
            {TIER_SEQUENCE.map((tier) => (
              <button
                key={tier}
                type="button"
                role="tab"
                aria-selected={tier === tierTab}
                className={`tier-tab${tier === tierTab ? " active" : ""}`}
                onClick={() => changeTierTab(tier)}
                title={`Медали уровня «${TIER_LABELS[tier]}»`}
              >
                <span className={`tier-tab-dot ${TIER_DOT_CLASS[tier]}`} />
                {TIER_LABELS[tier]}
              </button>
            ))}
          </div>
          <span className={`achv-summary achv-recalc${dimmed ? " is-busy" : ""}`}>
            {view.gold > 0 && <span className="achv-summary-item achv-summary-gold">🥇 {view.gold}</span>}
            {view.silver > 0 && <span className="achv-summary-item achv-summary-silver">🥈 {view.silver}</span>}
            {view.bronze > 0 && <span className="achv-summary-item achv-summary-bronze">🥉 {view.bronze}</span>}
            <span className="muted">
              {view.rows.length} из {view.inTier}
            </span>
          </span>
        </div>
      </div>
      {view.rows.length === 0 ? (
        <p className={`muted achv-recalc${dimmed ? " is-busy" : ""}`}>
          На уровне «{TIER_LABELS[tierTab]}» медалей пока нет. Загляни в челленджи ниже: на лёгком уровне бронза у
          многих начинается с одного-двух результатов.
        </p>
      ) : (
        <div className={`achv-badges achv-recalc${dimmed ? " is-busy" : ""}`}>
          {view.rows.map((row) => (
            <a
              key={row.code}
              className="achv-badge"
              href={`#challenge-${row.code}`}
              title={row.achievedAt ? `Получено ${formatDate(row.achievedAt)}` : undefined}
              onClick={(event) => {
                // Нативный переход по #hash прыгает мгновенно и без отступа под
                // липкую шапку — ведём скролл сами (см. jumpToChallenge).
                event.preventDefault();
                onJump(row.code);
              }}
            >
              <TierRing tier={row.tier} title={`Уровень «${TIER_LABELS[row.tier] ?? row.tier}»`}>
                <MedalIcon icon={row.icon} level={row.level} size="lg" />
              </TierRing>
              <span className="achv-badge-title">{row.title}</span>
              <span className={`achv-badge-level achv-badge-level-${row.level}`}>{LEVEL_LABELS[row.level]}</span>
              {row.achievedAt && <span className="achv-badge-date">{formatDate(row.achievedAt)}</span>}
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

  // Куда только что перешли из витрины медалей — карточка коротко подсвечивается,
  // чтобы после прокрутки было видно, на что именно приехали.
  const [jumpTarget, setJumpTarget] = useState<string | null>(null);
  const jumpTimer = useRef<number | undefined>(undefined);

  const jumpToChallenge = useCallback((code: string) => {
    const anchor = document.getElementById(`challenge-${code}`);
    if (!anchor) {
      return;
    }
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    // Прокручиваем вручную, а не scrollIntoView: над контентом залипают ДВЕ
    // полосы — шапка портала и фильтр систем, — и их суммарную высоту нельзя
    // задать статикой в scroll-margin-top (фильтр есть не на каждой странице,
    // а его высота зависит от числа систем и переносов строк).
    const top = anchor.getBoundingClientRect().top + window.scrollY - stickyOffsetTop();
    window.scrollTo({ top: Math.max(top, 0), behavior: reduceMotion ? "auto" : "smooth" });
    // Хэш обновляем без прыжка: history вместо location.hash.
    window.history.replaceState(null, "", `#challenge-${code}`);
    setJumpTarget(code);
    window.clearTimeout(jumpTimer.current);
    jumpTimer.current = window.setTimeout(() => setJumpTarget(null), 1800);
  }, []);

  // Прямая ссылка вида /new/achievements#challenge-streak: в момент загрузки
  // страницы карточек ещё нет в DOM (данные приходят позже), поэтому браузер
  // никуда не переходит — доводим скролл сами, когда данные отрисованы.
  useEffect(() => {
    const hash = window.location.hash;
    if (!hash.startsWith("#challenge-")) {
      return;
    }
    const timer = window.setTimeout(() => jumpToChallenge(hash.slice("#challenge-".length)), 80);
    return () => window.clearTimeout(timer);
  }, [jumpToChallenge]);

  useEffect(() => () => window.clearTimeout(jumpTimer.current), []);

  return (
    <>
      <BadgesShowcase data={data} onJump={jumpToChallenge} recalculating={platformSwitching} />

      {goalsSection}

      <section className="achv-section">
        <div className="achv-section-head">
          <h2 className="section-title">Челленджи</h2>
          <span className="muted">
            {pluralizeRu(data.summary.total, ["челлендж", "челленджа", "челленджей"])} · три уровня сложности, в
            каждом бронза, серебро и золото
          </span>
        </div>
        <TierLegend />
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
                <div className={`challenge-grid achv-recalc${platformSwitching ? " is-busy" : ""}`}>
                  {grouped[category].map((challenge) => (
                    // key стабильный (только код): карточка переиспользуется при
                    // смене фильтра систем, поэтому раскрытые «Детали» остаются
                    // раскрытыми. Вкладку сложности на новый тир переводит эффект
                    // внутри ChallengeCard, а не перемонтирование.
                    <div
                      key={challenge.code}
                      id={`challenge-${challenge.code}`}
                      className={`challenge-anchor${jumpTarget === challenge.code ? " is-jump-target" : ""}`}
                    >
                      <ChallengeCard
                        challenge={challenge}
                        personal={personal}
                        platformFilter={platformFilter}
                      />
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

