import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { PortalSectionShell, SectionSidebarNav } from "../portal/PortalSectionShell";
import { RATINGS_NAV } from "./ratingsNav";
import {
  GENDERED_METRICS,
  getLeaderboard,
  getMyLeaderboardRow,
  PLATFORM_LABELS,
  type LeaderboardGender,
  type LeaderboardMetric,
  type LeaderboardResponse,
  type LeaderboardRow,
  type MyLeaderboardRow,
} from "./leaderboardsApi";
import { unitLabel } from "./pluralize";
import { RatingsLoginBanner } from "./RatingsLoginBanner";
import "./leaderboards.css";

const PAGE_STEP = 100;
const SCROLL_TOP_THRESHOLD = 480;

const GENDER_TABS: { value: LeaderboardGender; label: string }[] = [
  { value: "all", label: "Абсолют" },
  { value: "male", label: "Мужчины" },
  { value: "female", label: "Женщины" },
];

type LeaderboardPageProps = {
  metric: LeaderboardMetric;
};

type SortKey = "rank" | "total" | string;

const METRIC_CRUMBS: Record<LeaderboardMetric, { section: string; label: string }> = {
  runs: { section: "Бегуны", label: "Количество пробежек" },
  volunteering: { section: "Волонтёры", label: "Количество волонтёрств" },
  locations: { section: "Паркран-туристы", label: "Уникальные локации" },
  wins: { section: "Бегуны", label: "Количество первых мест" },
  win_locations: { section: "Паркран-туристы", label: "Локации с первым местом" },
};

const TOP_LOCATION_HINT =
  "Число рядом — сколько раз участник был первым именно на этой локации, " +
  "а не сколько раз там бегал.";

function InfoHint({ text }: { text: string }) {
  return (
    <StatHintTooltip text={text} className="lb-info-hint">
      <span aria-hidden="true">i</span>
    </StatHintTooltip>
  );
}

function TopWinLocation({
  row,
}: {
  row: { home_location?: string | null; home_location_wins?: number | null };
}) {
  if (!row.home_location) {
    return <span className="lb-zero">—</span>;
  }
  return (
    <span className="lb-home">
      {row.home_location}
      {row.home_location_wins != null && row.home_location_wins > 1 && (
        <span className="lb-home-count"> ×{row.home_location_wins}</span>
      )}
    </span>
  );
}

function formatDate(value: string | null): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("ru-RU");
}

function DeltaSlot({ delta }: { delta: number }) {
  // Слот фиксированной ширины: дельта не сдвигает цифры в колонке.
  return <span className="lb-delta">{delta > 0 ? `+${delta}` : ""}</span>;
}

function RankDelta({ delta }: { delta: number | null }) {
  if (!delta) {
    return null;
  }
  const up = delta > 0;
  return (
    <span className={up ? "lb-rank-delta lb-rank-up" : "lb-rank-delta lb-rank-down"}>
      {up ? "▲" : "▼"}
      {Math.abs(delta)}
    </span>
  );
}

function CellValue({ cell }: { cell?: { value: number; delta: number } }) {
  if (!cell || cell.value === 0) {
    return (
      <span className="lb-cell">
        <span className="lb-zero">—</span>
        <DeltaSlot delta={0} />
      </span>
    );
  }
  return (
    <span className="lb-cell">
      {cell.value}
      <DeltaSlot delta={cell.delta} />
    </span>
  );
}

function ParticipantName({ row }: { row: { display_name: string | null; site_serial_id: number | null } }) {
  const name = row.display_name?.trim() || "Участник";
  if (row.site_serial_id != null) {
    return (
      <a className="lb-name lb-name-link" href={`/users/${row.site_serial_id}`}>
        {name}
      </a>
    );
  }
  return <span className="lb-name">{name}</span>;
}

function sortValue(row: LeaderboardRow, key: SortKey): number {
  if (key === "rank") {
    return -row.rank;
  }
  if (key === "total") {
    return row.total;
  }
  return row.platforms[key]?.value ?? 0;
}

export function LeaderboardPage({ metric }: LeaderboardPageProps) {
  const [data, setData] = useState<LeaderboardResponse | null>(null);
  const [me, setMe] = useState<MyLeaderboardRow | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("rank");
  const [gender, setGender] = useState<LeaderboardGender>("all");
  const [visibleCount, setVisibleCount] = useState(PAGE_STEP);
  const [showScrollTop, setShowScrollTop] = useState(false);
  const myRowRef = useRef<HTMLTableRowElement | null>(null);

  useEffect(() => {
    const handleScroll = () => {
      setShowScrollTop(window.scrollY > SCROLL_TOP_THRESHOLD);
    };
    handleScroll();
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  const hasGenderSplit = GENDERED_METRICS.includes(metric);
  const effectiveGender = hasGenderSplit ? gender : "all";

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [board, myRow] = await Promise.all([
        getLeaderboard(metric, 1000, effectiveGender),
        getMyLeaderboardRow(metric, effectiveGender).catch(() => null),
      ]);
      setData(board);
      setMe(myRow);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить рейтинг");
    } finally {
      setLoading(false);
    }
  }, [metric, effectiveGender]);

  useEffect(() => {
    void load();
  }, [load]);

  // Метрика сменилась (напр. переход между рейтингами) — сбрасываем пол в «Абсолют».
  useEffect(() => {
    setGender("all");
  }, [metric]);

  useEffect(() => {
    setVisibleCount(PAGE_STEP);
  }, [query, sortKey, effectiveGender]);

  const columns = data?.platform_columns ?? [];

  // Строка залогиненного всегда присутствует в списке: если её нет среди
  // загруженного топа (за топ-1000 или приватный профиль), вставляем синтетическую.
  const allRows = useMemo<LeaderboardRow[]>(() => {
    if (!data) {
      return [];
    }
    if (!me || !me.included || me.rank == null) {
      return data.rows;
    }
    const present = data.rows.some((row) => row.site_serial_id === me.site_serial_id);
    if (present) {
      return data.rows;
    }
    const myRow: LeaderboardRow = {
      rank: me.rank,
      rank_delta: me.rank_delta ?? 0,
      display_name: me.display_name ?? "Вы",
      site_serial_id: me.site_serial_id,
      platforms: me.platforms,
      total: me.total,
      total_delta: me.total_delta,
      home_location: me.home_location,
      home_location_wins: me.home_location_wins,
    };
    return [...data.rows, myRow];
  }, [data, me]);

  const rows = useMemo<LeaderboardRow[]>(() => {
    let result = allRows.slice().sort((a, b) => sortValue(b, sortKey) - sortValue(a, sortKey));
    const normalized = query.trim().toLowerCase();
    if (normalized) {
      result = result.filter((row) =>
        (row.display_name ?? "").toLowerCase().includes(normalized),
      );
    }
    return result;
  }, [allRows, sortKey, query]);

  const myIndex = useMemo(() => {
    if (!me) {
      return -1;
    }
    return rows.findIndex((row) => row.site_serial_id === me.site_serial_id);
  }, [rows, me]);

  const showMyRow = useCallback(() => {
    if (myIndex < 0) {
      return;
    }
    setVisibleCount((count) => (myIndex >= count ? Math.ceil((myIndex + 1) / PAGE_STEP) * PAGE_STEP : count));
    window.setTimeout(() => {
      myRowRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
    }, 60);
  }, [myIndex]);

  const crumbs = METRIC_CRUMBS[metric];
  const visibleRows = rows.slice(0, visibleCount);
  const nextChunkEnd = Math.min(visibleCount + PAGE_STEP, rows.length);

  const headerCell = (key: SortKey, label: string, className: string) => (
    <th
      key={key}
      className={`${className} lb-sortable${sortKey === key ? " lb-sorted" : ""}`}
      onClick={() => setSortKey(key)}
      title="Сортировать по этому столбцу"
    >
      {label}
      {sortKey === key && <span className="lb-sort-mark" aria-hidden>▾</span>}
    </th>
  );

  return (
    <PortalSectionShell sidebar={<SectionSidebarNav title="Рейтинги" items={RATINGS_NAV} />}>
      <div className="lb-page">
        <nav className="lb-breadcrumb">
          <a href="/ratings">← Все рейтинги</a>
          <span aria-hidden> / </span>
          <span>
            {crumbs.section} · {crumbs.label}
          </span>
        </nav>

        {loading && <p className="muted">Считаем рейтинг… Первый расчёт может занять до минуты.</p>}
        {error && (
          <div className="lb-error">
            <p>{error}</p>
            <button type="button" className="btn btn-sm" onClick={() => void load()}>
              Повторить
            </button>
          </div>
        )}

        {data && !loading && (
          <>
            <header className="lb-header">
              <h1>{data.title}</h1>
              <p className="lb-description">{data.description}</p>
              <p className="lb-meta muted">
                Данные на {formatDate(data.latest_event_date)} · число рядом со значением (например, +1)
                — изменение за последнюю неделю
              </p>
            </header>

            <RatingsLoginBanner />

            <div className="lb-controls-row">
              <div className="lb-controls-left">
                {hasGenderSplit && (
                  <div className="lb-gender">
                    <div className="lb-gender-tabs" role="tablist" aria-label="Зачёт по полу">
                      {GENDER_TABS.map((tab) => (
                        <button
                          key={tab.value}
                          type="button"
                          role="tab"
                          aria-selected={gender === tab.value}
                          className={`lb-gender-tab${gender === tab.value ? " lb-gender-tab-active" : ""}`}
                          onClick={() => setGender(tab.value)}
                        >
                          {tab.label}
                        </button>
                      ))}
                    </div>
                    {effectiveGender !== "all" && (
                      <p className="lb-gender-note muted">parkrun в разбивку по полу не входит.</p>
                    )}
                  </div>
                )}
              </div>
              <div className="lb-controls-right">
                <input
                  className="lb-search"
                  type="search"
                  placeholder="Поиск по имени…"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
              </div>
            </div>

            {me && !(!me.included && me.gender_mismatch) && (
              <section
                className={me.included ? "lb-me" : "lb-me lb-me-out"}
                aria-label="Ваша строка в рейтинге"
              >
                {me.included ? (
                  <div className="lb-me-row">
                    <span className="lb-me-rank">
                      {me.rank}
                      <RankDelta delta={me.rank_delta} />
                    </span>
                    <span className="lb-me-name">{me.display_name ?? "Вы"}</span>
                    <span className="lb-me-values">
                      {columns.map((code) => (
                        <span key={code} className="lb-me-value">
                          <span className="lb-me-platform">{PLATFORM_LABELS[code] ?? code}</span>
                          <CellValue cell={me.platforms[code]} />
                        </span>
                      ))}
                      <span className="lb-me-value lb-me-total">
                        <span className="lb-me-platform">Всего</span>
                        <span className="lb-cell">
                          {me.total}
                          <DeltaSlot delta={me.total_delta} />
                        </span>
                      </span>
                      {metric === "wins" && me.home_location && (
                        <span className="lb-me-value">
                          <span className="lb-me-platform">
                            Топ-локация <InfoHint text={TOP_LOCATION_HINT} />
                          </span>
                          <TopWinLocation row={me} />
                        </span>
                      )}
                    </span>
                    {myIndex >= 0 && (
                      <button type="button" className="btn btn-ghost btn-sm" onClick={showMyRow}>
                        Показать в таблице
                      </button>
                    )}
                  </div>
                ) : (
                  <p className="lb-me-threshold">
                    Вы появитесь в рейтинге после достижения {me.threshold}{" "}
                    {unitLabel(metric, me.threshold)} — сейчас у вас {me.total}.
                  </p>
                )}
              </section>
            )}

            <div className="table-wrap lb-table-wrap">
              <table className={`data-table lb-table${metric === "wins" ? " lb-table-wins" : ""}`}>
                <thead>
                  <tr>
                    {headerCell("rank", "Место", "lb-col-rank")}
                    <th>Участник</th>
                    {columns.map((code) =>
                      headerCell(code, PLATFORM_LABELS[code] ?? code, "lb-col-num"),
                    )}
                    {headerCell("total", "Всего", "lb-col-num lb-col-total")}
                    {metric === "wins" && (
                      <th className="lb-col-home">
                        Топ-локация <InfoHint text={TOP_LOCATION_HINT} />
                      </th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.map((row, index) => {
                    const isMe = me != null && row.site_serial_id === me.site_serial_id;
                    return (
                      <tr
                        key={`${row.rank}-${row.display_name}-${row.site_serial_id ?? index}`}
                        ref={isMe ? myRowRef : undefined}
                        className={isMe ? "lb-row-me" : undefined}
                      >
                        <td className="lb-col-rank">
                          <span className="lb-rank">
                            {row.rank}
                            <RankDelta delta={row.rank_delta} />
                          </span>
                        </td>
                        <td>
                          <ParticipantName row={row} />
                        </td>
                        {columns.map((code) => (
                          <td key={code} className="lb-col-num">
                            <CellValue cell={row.platforms[code]} />
                          </td>
                        ))}
                        <td className="lb-col-num lb-col-total">
                          <span className="lb-cell lb-total">
                            {row.total}
                            <DeltaSlot delta={row.total_delta} />
                          </span>
                        </td>
                        {metric === "wins" && (
                          <td className="lb-col-home">
                            <TopWinLocation row={row} />
                          </td>
                        )}
                      </tr>
                    );
                  })}
                  {visibleRows.length === 0 && (
                    <tr>
                      <td colSpan={columns.length + (metric === "wins" ? 4 : 3)} className="muted">
                        Ничего не найдено
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            {rows.length > visibleCount && (
              <div className="lb-more">
                <button
                  type="button"
                  className="btn"
                  onClick={() => setVisibleCount((count) => count + PAGE_STEP)}
                >
                  Показать ещё (места {visibleCount + 1}–{nextChunkEnd})
                </button>
              </div>
            )}
          </>
        )}
        {showScrollTop && (
          <button
            type="button"
            className="lb-scroll-top"
            aria-label="Наверх страницы"
            title="Наверх страницы"
            onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
          >
            ↑
          </button>
        )}
      </div>
    </PortalSectionShell>
  );
}
