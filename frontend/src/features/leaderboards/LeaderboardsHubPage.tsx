import { useEffect, useMemo, useState } from "react";
import { PlatformFilter as PlatformFilterControl } from "../../components/filters/PlatformFilter";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import {
  ADMIN_ONLY_METRICS,
  getLeaderboard,
  getMyLeaderboardRow,
  METRIC_VALUE_UNIT,
  PLATFORM_LABELS,
  type LeaderboardMetric,
  type LeaderboardResponse,
  type MyLeaderboardRow,
  type PlatformFilter,
} from "./leaderboardsApi";
import { formatInt } from "../../lib/format";
import { useOptionalUser } from "../../lib/useOptionalUser";
import { unitLabel } from "./pluralize";
import { RatingsLoginBanner } from "./RatingsLoginBanner";
import {
  getLocationRecords,
  type LocationRecordRow,
  type LocationRecordsPlatform,
} from "./locationRecordsApi";
import { formatFinishTime } from "./formatFinishTime";
import { getFastestRating, type FastestRatingResponse } from "./fastestApi";
import { getRegionsRating, type RegionRatingRow, type RegionsPlatform } from "./regionsApi";
import { surnameFirst } from "../../lib/personName";
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

type HubSection = {
  emoji: string;
  title: string;
  live: LiveCard[];
  /**
   * Рейтинг быстрых живёт не метрикой лидерборда, а своим API: строка там —
   * забег, а не участник со счётчиками. Карточку рисуем отдельным компонентом,
   * поэтому секция помечает её флагом, а не ещё одной записью в live.
   */
  fastest?: boolean;
};

// Только готовые рейтинги. Карточек-анонсов «скоро» здесь нет намеренно
// (решение Дмитрия 09.08.2026): раздел показывает то, что уже работает, а
// планы живут в бэклоге «Рейтинги» — новый рейтинг появляется на хабе в тот
// же момент, когда становится доступен всем. Секция «Локации» (Р18/Р19) по
// этой же причине пока отсутствует целиком — в ней нет ни одного живого
// рейтинга.
const SECTIONS: HubSection[] = [
  {
    emoji: "🏃",
    title: "Бегуны",
    live: [
      { metric: "runs", href: "/ratings/runs", title: "Количество пробежек" },
      { metric: "wins", href: "/ratings/wins", title: "Количество первых мест" },
    ],
    fastest: true,
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
  },
  {
    emoji: "🧭",
    title: "Паркран-туристы",
    live: [
      { metric: "locations", href: "/ratings/locations", title: "Уникальные локации" },
      { metric: "openings", href: "/ratings/openings", title: "Открытия локаций" },
      { metric: "win_locations", href: "/ratings/win-locations", title: "Локации с первым местом" },
      { metric: "home_distance", href: "/ratings/home-distance", title: "Дальность от дома" },
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
  const valueUnit = METRIC_VALUE_UNIT[card.metric];
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
              <span className="lb-hub-rank-name">{surnameFirst(row.display_name) || "Участник"}</span>
              <span className="lb-hub-rank-value">
                {formatInt(row.total)}
                {valueUnit && <span className="lb-unit">{valueUnit}</span>}
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
                {formatInt(me.total)}
                {valueUnit && <span className="lb-unit">{valueUnit}</span>}
                <DeltaSlot delta={me.total_delta} />
              </span>
            </div>
          ) : (
            <p className="lb-hub-me-threshold">
              Вы появитесь после {me.threshold} {unitLabel(card.metric, me.threshold)} — сейчас у вас{" "}
              {formatInt(me.total)}{valueUnit ? ` ${valueUnit}` : ""}.
            </p>
          )}
        </div>
      )}

      <span className="lb-hub-see-all">Смотреть топ →</span>
    </a>
  );
}

/**
 * Карточка рейтинга быстрых: топ-3 результата и время вместо счётчика. Строки
 * «Вы» тут нет намеренно — личный рекорд человек видит и в своём кабинете, а
 * карточка на хабе про то, чтобы захотелось открыть таблицу.
 */
function FastestRatingCard({ platform }: { platform: PlatformFilter }) {
  const [board, setBoard] = useState<FastestRatingResponse | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    let cancelled = false;
    setState("loading");
    setBoard(null);
    getFastestRating(
      { mode: "results", platform, gender: "all", ageGroup: "all", year: "all" },
      HUB_TOP_N,
    )
      .then((payload) => {
        if (!cancelled) {
          setBoard(payload);
          setState("ready");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setState("error");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [platform]);

  return (
    <a className="lb-hub-card lb-hub-card-live" href="/ratings/fastest">
      <div className="lb-hub-card-top">
        <span className="lb-hub-card-title">Самые быстрые</span>
      </div>

      {state === "loading" && <p className="lb-hub-loading muted">Считаем…</p>}
      {state === "error" && <p className="lb-hub-loading muted">Не удалось загрузить</p>}

      {board && (
        <div className="lb-hub-top3">
          <p className="lb-hub-top3-label">Топ-3 результата</p>
          {board.rows.map((row, index) => (
            <div className="lb-hub-rank-row" key={row.row_key + row.event_date}>
              <span className={`lb-hub-rank-chip lb-hub-rank-${RANK_TIER[index] ?? "silver"}`}>
                {row.rank}
              </span>
              <span className="lb-hub-rank-name">{surnameFirst(row.display_name) || "Участник"}</span>
              <span className="lb-hub-rank-value">
                {formatFinishTime(row.finish_time_sec, row.finish_time_display)}
              </span>
            </div>
          ))}
        </div>
      )}

      <span className="lb-hub-see-all">Смотреть топ →</span>
    </a>
  );
}

/** Часы в «00:14:30» на карточке лишние: пятёрку никто не бежит дольше часа. */
function hubRecordTime(display: string | null): string {
  if (!display) {
    return "—";
  }
  return display.startsWith("00:") ? display.slice(3) : display;
}

/**
 * Карточка «Рекорды локаций» — первая живая карточка секции «Локации».
 * Строка здесь локация, а не участник, поэтому у неё свой фетч и свой вид:
 * общий LiveRatingCard умеет только лидерборды людей.
 */
function LocationRecordsHubCard({ platform }: { platform: PlatformFilter }) {
  const [rows, setRows] = useState<LocationRecordRow[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setRows(null);
    setError(false);
    getLocationRecords({
      scope: "absolute",
      gender: "male",
      platform: platform as LocationRecordsPlatform,
    })
      .then((payload) => {
        if (!cancelled) {
          setRows(payload.rows.slice(0, 3));
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [platform]);

  return (
    <a className="lb-hub-card lb-hub-card-live" href="/ratings/location-records">
      <div className="lb-hub-card-top">
        <span className="lb-hub-card-title">Рекорды локаций</span>
      </div>
      {rows === null && !error && <p className="lb-hub-loading muted">Считаем…</p>}
      {error && <p className="lb-hub-loading muted">Не удалось загрузить</p>}
      {rows && (
        <div className="lb-hub-top3">
          <p className="lb-hub-top3-label">Самые быстрые трассы · мужчины</p>
          {rows.map((row, index) => (
            <div className="lb-hub-rank-row" key={row.slug}>
              <span className={`lb-hub-rank-chip lb-hub-rank-${RANK_TIER[index] ?? "silver"}`}>
                {row.place}
              </span>
              <span className="lb-hub-rank-name">{row.name}</span>
              <span className="lb-hub-rank-value">{hubRecordTime(row.finish_time_display)}</span>
            </div>
          ))}
        </div>
      )}
      <span className="lb-hub-see-all">Смотреть топ →</span>
    </a>
  );
}

/**
 * Карточка «Локации по регионам»: строка — регион, а не участник, поэтому у
 * неё свой фетч. parkrun этот рейтинг не считает (закрыт с 2022, регион у его
 * строк почти везде пуст) — при выборе parkrun на хабе карточка честно
 * показывает общий зачёт и подписывает это, как остальные.
 */
function RegionsHubCard({ platform }: { platform: PlatformFilter }) {
  const [rows, setRows] = useState<RegionRatingRow[] | null>(null);
  const [error, setError] = useState(false);
  const supported = platform !== "parkrun";

  useEffect(() => {
    let cancelled = false;
    setRows(null);
    setError(false);
    getRegionsRating(supported ? (platform as RegionsPlatform) : "all")
      .then((payload) => {
        if (!cancelled) {
          setRows(payload.regions.slice(0, HUB_TOP_N));
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [platform, supported]);

  return (
    <a className="lb-hub-card lb-hub-card-live" href="/ratings/regions">
      <div className="lb-hub-card-top">
        <span className="lb-hub-card-title">Локации по регионам</span>
      </div>
      {rows === null && !error && <p className="lb-hub-loading muted">Считаем…</p>}
      {error && <p className="lb-hub-loading muted">Не удалось загрузить</p>}
      {!supported && (
        <p className="lb-hub-card-fallback muted">
          {HUB_PLATFORM_TAB_LABELS.parkrun} не участвует — показан общий зачёт
        </p>
      )}
      {rows && (
        <div className="lb-hub-top3">
          <p className="lb-hub-top3-label">Больше всего площадок</p>
          {rows.map((row, index) => (
            <div className="lb-hub-rank-row" key={row.name}>
              <span className={`lb-hub-rank-chip lb-hub-rank-${RANK_TIER[index] ?? "silver"}`}>
                {row.place}
              </span>
              <span className="lb-hub-rank-name">{row.name}</span>
              <span className="lb-hub-rank-value">{formatInt(row.locations)}</span>
            </div>
          ))}
        </div>
      )}
      <span className="lb-hub-see-all">Смотреть топ →</span>
    </a>
  );
}

export function LeaderboardsHubPage() {
  const [platform, setPlatform] = useState<PlatformFilter>("all");
  // Закрытые рейтинги показываем только админу: карточка тянет данные, а API
  // отдаёт их лишь ему — остальным она молча висела бы «Считаем…».
  const viewer = useOptionalUser();
  const sections = useMemo(
    () =>
      SECTIONS.map((section) => ({
        ...section,
        live: section.live.filter(
          (card) => viewer?.is_admin || !ADMIN_ONLY_METRICS.includes(card.metric),
        ),
      })).filter((section) => section.live.length > 0),
    [viewer],
  );

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

        {/* Фильтр в такой же панели с рамкой, что и на страницах рейтингов:
            голый переключатель над карточками выбивался из общего вида. */}
        <div className="lb-controls-left lb-hub-controls">
        <PlatformFilterControl
          mode="single"
          value={platform}
          onChange={(next) => setPlatform(next as typeof platform)}
          hint={<InfoHint text={HUB_PLATFORM_FILTER_HINT} />}
          ariaLabel="Смотреть по системе"
          options={HUB_PLATFORM_OPTIONS.filter((value) => value !== "all").map((value) => ({
            code: value,
            label: HUB_PLATFORM_TAB_LABELS[value] ?? value,
          }))}
        />
        </div>

        {sections.map((section) => (
          <section key={section.title} className="lb-hub-section">
            <h2>
              <span aria-hidden>{section.emoji}</span> {section.title}
            </h2>
            <div className="lb-hub-cards">
              {section.live.map((card) => (
                <LiveRatingCard key={card.metric} card={card} platform={platform} />
              ))}
              {section.fastest && <FastestRatingCard platform={platform} />}
            </div>
          </section>
        ))}

        {/* Секция «Локации»: здесь рейтингуются сами площадки и их география, а
            не люди (Р18/Р19 из бэклога ещё в плане). */}
        <section className="lb-hub-section">
          <h2>
            <span aria-hidden>📍</span> Локации
          </h2>
          <div className="lb-hub-cards">
            <LocationRecordsHubCard platform={platform} />
            <RegionsHubCard platform={platform} />
          </div>
        </section>
      </div>
    </PortalSectionShell>
  );
}
