import { useEffect, useState } from "react";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import {
  getLeaderboard,
  getMyLeaderboardRow,
  PLATFORM_LABELS,
  type LeaderboardMetric,
  type LeaderboardResponse,
  type MyLeaderboardRow,
  type PlatformFilter,
} from "./leaderboardsApi";
import { unitLabel } from "./pluralize";
import { RatingsLoginBanner } from "./RatingsLoginBanner";
import "./leaderboards.css";

const HUB_TOP_N = 3;

// Тот же фильтр «по системе», что и на странице конкретного рейтинга —
// здесь он один на весь хаб и сразу пересчитывает топ-3 и «моё место» во
// всех живых карточках (решение Дмитрия 01.08.2026: хотел его именно на
// главной «Рейтингов», не только внутри отдельных таблиц). Полный список
// кнопок захардкожен (не с бэкенда, как на странице метрики) — на хабе
// фильтр общий сразу для нескольких рейтингов с разным набором систем;
// карточка, которой выбранная система не подходит (parkrun у волонтёрского
// туризма), сама откатывается на общий зачёт и подписывает это под собой.
const HUB_PLATFORM_OPTIONS: PlatformFilter[] = ["all", "five_verst", "s95", "runpark", "parkrun"];
const HUB_PLATFORM_TAB_LABELS: Record<string, string> = { all: "Все", ...PLATFORM_LABELS };

const HUB_PLATFORM_FILTER_HINT =
  "Пересчитывает топ-3 и ваше место по одной системе вместо общего зачёта. " +
  "Если система не участвует в конкретном рейтинге — карточка покажет общий зачёт.";

function InfoHint({ text }: { text: string }) {
  return (
    <StatHintTooltip text={text} className="lb-info-hint">
      <span aria-hidden="true">i</span>
    </StatHintTooltip>
  );
}

type LiveCard = {
  metric: LeaderboardMetric;
  href: string;
  title: string;
};

type SoonCard = {
  title: string;
  description: string;
};

type HubSection = {
  emoji: string;
  title: string;
  live: LiveCard[];
  soon: SoonCard[];
};

const SECTIONS: HubSection[] = [
  {
    emoji: "🏃",
    title: "Бегуны",
    live: [
      { metric: "runs", href: "/ratings/runs", title: "Количество пробежек" },
      { metric: "wins", href: "/ratings/wins", title: "Количество первых мест" },
    ],
    soon: [
      { title: "Самые быстрые", description: "Лучшие результаты и бегуны М/Ж за всю историю." },
      { title: "Серии суббот", description: "Самые длинные серии подряд — текущие и исторические." },
    ],
  },
  {
    emoji: "🤝",
    title: "Волонтёры",
    live: [
      { metric: "volunteering", href: "/ratings/volunteering", title: "Количество волонтёрств" },
      {
        metric: "volunteer_locations",
        href: "/ratings/volunteer-locations",
        title: "Уникальные локации",
      },
      {
        metric: "volunteer_roles",
        href: "/ratings/volunteer-roles",
        title: "Мультиволонтёр — разнообразие ролей",
      },
    ],
    soon: [
      {
        title: "Дуализм",
        description: "Кто одинаково силён и как бегун, и как волонтёр.",
      },
    ],
  },
  {
    emoji: "🧭",
    title: "Паркран-туристы",
    live: [
      { metric: "locations", href: "/ratings/locations", title: "Уникальные локации" },
      { metric: "win_locations", href: "/ratings/win-locations", title: "Локации с первым местом" },
    ],
    soon: [
      { title: "Дальность от дома", description: "Кто уезжает бегать дальше всех от домашней локации." },
      { title: "Гео-коллекционер", description: "Уникальные регионы и города." },
    ],
  },
  {
    emoji: "📍",
    title: "Локации",
    live: [],
    soon: [
      { title: "Посещаемость локаций", description: "Самые массовые и быстрорастущие площадки." },
      { title: "Быстрые трассы", description: "Где бегут быстрее всего." },
    ],
  },
];

const RANK_TIER = ["gold", "silver", "bronze"] as const;

function DeltaSlot({ delta }: { delta: number }) {
  return <span className="lb-hub-delta">{delta > 0 ? `+${delta}` : ""}</span>;
}

type CardState = {
  board: LeaderboardResponse | null;
  me: MyLeaderboardRow | null;
  loading: boolean;
  error: boolean;
};

function LiveRatingCard({ card, platform }: { card: LiveCard; platform: PlatformFilter }) {
  const [state, setState] = useState<CardState>({ board: null, me: null, loading: true, error: false });

  useEffect(() => {
    let cancelled = false;
    setState({ board: null, me: null, loading: true, error: false });
    Promise.all([
      getLeaderboard(card.metric, HUB_TOP_N, "all", 1, platform),
      getMyLeaderboardRow(card.metric, "all", 1, platform).catch(() => null),
    ])
      .then(([board, me]) => {
        if (!cancelled) {
          setState({ board, me, loading: false, error: false });
        }
      })
      .catch(() => {
        if (!cancelled) {
          setState({ board: null, me: null, loading: false, error: true });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [card.metric, platform]);

  const { board, me, loading, error } = state;
  // Бэкенд молча откатывает фильтр на общий зачёт, если система не участвует
  // в этом рейтинге (напр. parkrun у волонтёрского туризма) — сверяем с тем,
  // что реально пришло, а не с тем, что выбрано глобально на хабе.
  const fellBackToAll = platform !== "all" && board != null && board.platform === "all";

  return (
    <a className="lb-hub-card lb-hub-card-live" href={card.href}>
      <div className="lb-hub-card-top">
        <span className="lb-hub-card-title">{card.title}</span>
      </div>

      {loading && <p className="lb-hub-loading muted">Считаем…</p>}
      {error && <p className="lb-hub-loading muted">Не удалось загрузить</p>}
      {fellBackToAll && (
        <p className="lb-hub-card-fallback muted">
          {HUB_PLATFORM_TAB_LABELS[platform]} не участвует — показан общий зачёт
        </p>
      )}

      {board && (
        <div className="lb-hub-top3">
          <p className="lb-hub-top3-label">Топ-3</p>
          {board.rows.map((row, index) => (
            <div className="lb-hub-rank-row" key={`${row.rank}-${row.display_name ?? index}`}>
              <span className={`lb-hub-rank-chip lb-hub-rank-${RANK_TIER[index] ?? "silver"}`}>
                {row.rank}
              </span>
              <span className="lb-hub-rank-name">{row.display_name?.trim() || "Участник"}</span>
              <span className="lb-hub-rank-value">
                {row.total}
                <DeltaSlot delta={row.total_delta} />
              </span>
            </div>
          ))}
        </div>
      )}

      {me && board && (
        <div className="lb-hub-me">
          {me.included ? (
            <div className="lb-hub-rank-row lb-hub-rank-row-me">
              <span className="lb-hub-rank-chip lb-hub-rank-me">{me.rank}</span>
              <span className="lb-hub-rank-name">Вы</span>
              <span className="lb-hub-rank-value">
                {me.total}
                <DeltaSlot delta={me.total_delta} />
              </span>
            </div>
          ) : (
            <p className="lb-hub-me-threshold">
              Вы появитесь после {me.threshold} {unitLabel(card.metric, me.threshold)} — сейчас у вас{" "}
              {me.total}.
            </p>
          )}
        </div>
      )}

      <span className="lb-hub-see-all">Смотреть топ →</span>
    </a>
  );
}

function SoonRatingCard({ card }: { card: SoonCard }) {
  return (
    <div className="lb-hub-card lb-hub-card-soon">
      <span className="lb-hub-card-title">
        {card.title} <span className="lb-soon">скоро</span>
      </span>
      <span className="lb-hub-card-text">{card.description}</span>
    </div>
  );
}

export function LeaderboardsHubPage() {
  const [platform, setPlatform] = useState<PlatformFilter>("all");

  return (
    <PortalSectionShell sidebar={{ active: "ratings" }}>
      <div className="lb-page lb-hub">
        <header className="lb-header">
          <h1>Рейтинги</h1>
          <p className="lb-description">
            Сквозные лидерборды по всем беговым системам сразу. Здесь каждый найдёт рейтинг по
            душе: кто-то берёт числом стартов, кто-то скоростью, кто-то волонтёрит каждую
            субботу, а кто-то коллекционирует новые парки. Найдите себя — и посмотрите, кто
            впереди.
          </p>
        </header>

        <RatingsLoginBanner />

        <div className="lb-visits lb-hub-platform-filter">
          <span className="lb-visits-label">
            Система <InfoHint text={HUB_PLATFORM_FILTER_HINT} />
          </span>
          <div className="lb-gender-tabs" role="group" aria-label="Смотреть по системе">
            {HUB_PLATFORM_OPTIONS.map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={platform === value}
                className={`lb-gender-tab${platform === value ? " lb-gender-tab-active" : ""}`}
                onClick={() => setPlatform(value)}
              >
                {HUB_PLATFORM_TAB_LABELS[value] ?? value}
              </button>
            ))}
          </div>
        </div>

        {SECTIONS.map((section) => (
          <section key={section.title} className="lb-hub-section">
            <h2>
              <span aria-hidden>{section.emoji}</span> {section.title}
            </h2>
            <div className="lb-hub-cards">
              {section.live.map((card) => (
                <LiveRatingCard key={card.metric} card={card} platform={platform} />
              ))}
              {section.soon.map((card) => (
                <SoonRatingCard key={card.title} card={card} />
              ))}
            </div>
          </section>
        ))}
      </div>
    </PortalSectionShell>
  );
}
