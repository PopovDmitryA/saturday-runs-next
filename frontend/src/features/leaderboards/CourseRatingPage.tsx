// Рейтинг трасс: перепад высот и прямолинейность по трекам участников.
// Устройство страницы — как у остальных рейтингов: крошки, шапка, панель
// фильтров с поиском, сортируемая таблица и карточки на узком экране.

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRestorableState } from "../../hooks/useRestorableState";
import { PlatformBadge } from "../../components/PlatformBadge";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { useNarrowViewport } from "../../components/tableUx/useNarrowViewport";
import { formatInt } from "../../lib/format";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { RatingsLoginBanner } from "./RatingsLoginBanner";
import {
  COURSE_METRIC_LABELS,
  getCourseRating,
  type CourseRatingItem,
  type CourseRatingMetric,
  type CourseRatingResponse,
} from "./courseApi";

const PAGE_STEP = 100;

const METRIC_TABS: { value: CourseRatingMetric; label: string }[] = [
  { value: "elevation", label: COURSE_METRIC_LABELS.elevation },
  { value: "straightness", label: COURSE_METRIC_LABELS.straightness },
];

const METRIC_HINT =
  "Перепад — разница между низшей и высшей точкой трассы. Берём его, а не набор высоты: " +
  "набор у барометра пляшет от погоды, на одной трассе выходило от 7 до 42 м. " +
  "Прямолинейность — суммарный поворот за дистанцию: один полный оборот это 360°, " +
  "чем меньше градусов, тем прямее трасса.";

const COLUMN_HINTS: Record<string, string> = {
  value: "Столбец выбранной метрики: по нему и построен рейтинг.",
  distance:
    "Длина трассы по трекам — медиана всех замеров. Один трек меряет с точностью около 1,5%, " +
    "поэтому чем больше треков, тем ближе цифра к настоящей.",
  elevation:
    "Сумма всех подъёмов за пробежку по барометру часов. Зависит от погоды и прибора сильнее, " +
    "чем перепад: на одной трассе выходило от 7 до 42 м.",
  turns:
    "Сколько всего градусов трасса поворачивает за дистанцию. Направление движения берётся " +
    "каждые 10 метров, изменения складываются без учёта стороны; 360° — один полный оборот.",
  straight:
    "Самый длинный участок без заметных поворотов — место, где можно разогнаться и обогнать.",
  laps: "Сколько раз трасса повторяет один и тот же круг. Одна петля без повторов — это 1.",
  tracks: "Сколько треков учтено в расчёте. Наведите на число — увидите, от скольких разных людей.",
};

const EMPTY_HINT =
  "Локации без треков из рейтинга не убираем: по ним видно, где данных ещё нет. " +
  "Цифры появятся, когда участники приложат треки.";

type SortKey = "value" | "distance" | "elevation" | "turns" | "straight" | "laps" | "tracks" | "name";

type SortState = { key: SortKey; direction: "asc" | "desc" };

/** По умолчанию: величины — от большего, названия — по алфавиту. */
const DEFAULT_DIRECTION: Record<SortKey, SortState["direction"]> = {
  value: "desc",
  distance: "desc",
  elevation: "desc",
  turns: "desc",
  straight: "desc",
  laps: "desc",
  tracks: "desc",
  name: "asc",
};

function sortValue(row: CourseRatingItem, key: SortKey): number | string | null {
  switch (key) {
    case "value":
      return row.value;
    case "distance":
      return row.distance_m;
    case "elevation":
      return row.elevation_span_m;
    case "turns":
      return row.turn_sum_deg;
    case "straight":
      return row.longest_straight_m;
    case "laps":
      return row.lap_count;
    case "tracks":
      return row.tracks_count;
    default:
      return row.location_name.toLowerCase();
  }
}

function SortableHeader({
  label,
  sortKey,
  sort,
  onSort,
  className,
}: {
  label: string;
  sortKey: SortKey;
  sort: SortState;
  onSort: (key: SortKey) => void;
  className?: string;
}) {
  const active = sort.key === sortKey;
  const hint = COLUMN_HINTS[sortKey];
  return (
    <th
      className={`${className ?? ""} lb-sortable${active ? " lb-sorted" : ""}`}
      onClick={() => onSort(sortKey)}
      title="Сортировать по этому столбцу"
      aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}
      data-tap-tooltip="off"
    >
      {label}
      {hint && (
        // Клик по значку не должен сортировать — иначе подсказку не прочитать.
        <span className="lb-col-hint" onClick={(event) => event.stopPropagation()}>
          <StatHintTooltip text={hint}>
            <span aria-label="Как считается">ⓘ</span>
          </StatHintTooltip>
        </span>
      )}
      <span className="lb-sort-mark" aria-hidden>
        {active ? (sort.direction === "asc" ? "▴" : "▾") : "↕"}
      </span>
    </th>
  );
}

function formatKm(meters: number | null): string {
  return meters == null ? "—" : (meters / 1000).toFixed(2).replace(".", ",");
}

function formatMetric(metric: CourseRatingMetric, row: CourseRatingItem): string {
  if (row.value == null) {
    return "—";
  }
  return metric === "elevation"
    ? `${row.value.toFixed(1).replace(".", ",")} м`
    : `${formatInt(row.value)}°`;
}

function LocationCell({ row }: { row: CourseRatingItem }) {
  return (
    <>
      <a href={`/locations/${row.location_slug}`}>{row.location_name}</a>
      {row.city && row.city !== row.location_name && <span className="lb-muted"> · {row.city}</span>}
    </>
  );
}

export function CourseRatingPage() {
  const narrowViewport = useNarrowViewport();
  const [metric, setMetric] = useRestorableState<CourseRatingMetric>("courses.metric", "elevation");
  const [query, setQuery] = useRestorableState("courses.query", "");
  const [showEmpty, setShowEmpty] = useRestorableState("courses.empty", false);
  const [visibleCount, setVisibleCount] = useRestorableState("courses.visible", PAGE_STEP);
  const [sort, setSort] = useRestorableState<SortState>("courses.sort", {
    key: "value",
    direction: "desc",
  });
  const attachFloatingHead = useFloatingTableHead();

  const [data, setData] = useState<CourseRatingResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((value: CourseRatingMetric) => {
    setLoading(true);
    setError(null);
    getCourseRating(value)
      .then(setData)
      .catch((cause: Error) => setError(cause.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load(metric);
  }, [load, metric]);

  const toggleSort = useCallback(
    (key: SortKey) => {
      setSort((current) =>
        current.key === key
          ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
          : { key, direction: DEFAULT_DIRECTION[key] },
      );
    },
    [setSort],
  );

  const rows = useMemo(() => {
    if (!data) {
      return [];
    }
    const needle = query.trim().toLowerCase();
    const filtered = data.items.filter((item) => {
      if (!showEmpty && !item.has_data) {
        return false;
      }
      if (!needle) {
        return true;
      }
      return [item.location_name, item.city, item.region]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle));
    });

    // Прямолинейность читается от меньшего: чем меньше градусов, тем прямее.
    const direction =
      sort.key === "value" && metric === "straightness" && sort.direction === "desc"
        ? "asc"
        : sort.direction;
    const factor = direction === "asc" ? 1 : -1;

    return [...filtered].sort((left, right) => {
      const a = sortValue(left, sort.key);
      const b = sortValue(right, sort.key);
      // Локации без данных всегда в конце: иначе прочерки всплывали бы наверх.
      if (a == null && b == null) return 0;
      if (a == null) return 1;
      if (b == null) return -1;
      if (typeof a === "string" || typeof b === "string") {
        return String(a).localeCompare(String(b), "ru") * factor;
      }
      return (a - b) * factor;
    });
  }, [data, metric, query, showEmpty, sort]);

  const visibleRows = rows.slice(0, visibleCount);
  const metricLabel = metric === "elevation" ? "Перепад" : "Поворот";

  return (
    <PortalSectionShell sidebar={{ active: "ratings" }}>
      <div className="lb-page">
        <nav className="lb-breadcrumb">
          <a href="/ratings">← Все рейтинги</a>
          <span aria-hidden> / </span>
          <span>Локации · Трассы локаций</span>
        </nav>

        <header className="lb-header">
          <h1>Трассы локаций</h1>
          <p className="lb-description">
            Какие на самом деле трассы у субботних пятёрок: перепад высот и извилистость, посчитанные по
            трекам участников.
          </p>
        </header>

        <RatingsLoginBanner />

        <div className="lb-controls-row lb-controls-inline">
          <div className="lb-controls-left">
            <div className="lb-visits">
              <span className="lb-visits-label">
                Метрика{" "}
                <StatHintTooltip text={METRIC_HINT}>
                  <span aria-label="Как считается">ⓘ</span>
                </StatHintTooltip>
              </span>
              <div className="lb-gender-tabs" role="group" aria-label="Метрика">
                {METRIC_TABS.map((tab) => (
                  <button
                    key={tab.value}
                    type="button"
                    aria-pressed={metric === tab.value}
                    className={`lb-gender-tab${metric === tab.value ? " lb-gender-tab-active" : ""}`}
                    onClick={() => setMetric(tab.value)}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="lb-visits">
              <span className="lb-visits-label">
                Без треков{" "}
                <StatHintTooltip text={EMPTY_HINT}>
                  <span aria-label="Как считается">ⓘ</span>
                </StatHintTooltip>
              </span>
              <div className="lb-gender-tabs" role="group" aria-label="Локации без треков">
                <button
                  type="button"
                  aria-pressed={!showEmpty}
                  className={`lb-gender-tab${!showEmpty ? " lb-gender-tab-active" : ""}`}
                  onClick={() => setShowEmpty(false)}
                >
                  Скрыть
                </button>
                <button
                  type="button"
                  aria-pressed={showEmpty}
                  className={`lb-gender-tab${showEmpty ? " lb-gender-tab-active" : ""}`}
                  onClick={() => setShowEmpty(true)}
                >
                  Показать
                </button>
              </div>
            </div>

            {/* Поиск живёт внутри панели, как на остальных рейтингах, и стоит
                в той же строке, что вкладки. */}
            <div className="lb-controls-right">
              <span className="lb-visits-label">Поиск</span>
              <input
                className="lb-search"
                type="search"
                placeholder="Поиск по локации, городу, региону…"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>
          </div>
        </div>

        {data && (
          <p className="lb-description">
            Промерено локаций: <b>{data.with_data_count}</b> из {data.total_count}.
          </p>
        )}

        {loading && !data && <p className="muted">Считаем рейтинг…</p>}
        {error && (
          <div className="lb-error">
            <p>{error}</p>
            <button type="button" className="btn btn-sm" onClick={() => load(metric)}>
              Повторить
            </button>
          </div>
        )}

        {data && (
          <div className={`lb-page-body${loading ? " lb-refreshing" : ""}`}>
            {rows.length === 0 ? (
              <p className="muted">Ничего не нашлось — попробуйте другой запрос.</p>
            ) : narrowViewport ? (
              <div className="rowcards">
                {visibleRows.map((row) => (
                  <div className="rowcard" key={`${row.platform_code}-${row.location_slug}`}>
                    <div className="rowcard-rank">{row.position ?? "—"}</div>
                    <div className="rowcard-mid">
                      <div className="rowcard-title">
                        <LocationCell row={row} />
                      </div>
                      <div className="rowcard-sub">
                        {row.has_data
                          ? `${formatKm(row.distance_m)} км · ${row.lap_count ?? 1} кр. · треков ${row.tracks_count}`
                          : "треков пока нет"}
                      </div>
                    </div>
                    <div className="rowcard-right">
                      <div className="rowcard-value">{formatMetric(metric, row)}</div>
                      <div className="rowcard-sub">
                        <PlatformBadge code={row.platform_code} />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <TableWrap className="lb-table-wrap lb-table-wrap-flat" innerRef={attachFloatingHead}>
                <table className="data-table lb-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <SortableHeader label="Локация" sortKey="name" sort={sort} onSort={toggleSort} />
                      <SortableHeader label={metricLabel} sortKey="value" sort={sort} onSort={toggleSort} />
                      <SortableHeader label="Длина, км" sortKey="distance" sort={sort} onSort={toggleSort} />
                      <SortableHeader label="Набор" sortKey="elevation" sort={sort} onSort={toggleSort} />
                      <SortableHeader label="Поворот" sortKey="turns" sort={sort} onSort={toggleSort} />
                      <SortableHeader label="Прямая" sortKey="straight" sort={sort} onSort={toggleSort} />
                      <SortableHeader label="Кругов" sortKey="laps" sort={sort} onSort={toggleSort} />
                      <SortableHeader label="Треков" sortKey="tracks" sort={sort} onSort={toggleSort} />
                      <th>Система</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleRows.map((row) => (
                      <tr
                        key={`${row.platform_code}-${row.location_slug}`}
                        className={row.has_data ? "" : "lb-row-muted"}
                      >
                        <td>{row.position ?? "—"}</td>
                        <td>
                          <LocationCell row={row} />
                        </td>
                        <td className="num strong">{formatMetric(metric, row)}</td>
                        <td className="num">{formatKm(row.distance_m)}</td>
                        <td className="num">
                          {row.elevation_gain_m != null ? `${Math.round(row.elevation_gain_m)} м` : "—"}
                        </td>
                        <td className="num">
                          {row.turn_sum_deg != null ? `${formatInt(row.turn_sum_deg)}°` : "—"}
                        </td>
                        <td className="num">
                          {row.longest_straight_m != null ? `${Math.round(row.longest_straight_m)} м` : "—"}
                        </td>
                        <td className="num">{row.lap_count ?? "—"}</td>
                        <td className="num" title={`Участников: ${row.unique_user_count}`}>
                          {row.tracks_count || "—"}
                        </td>
                        <td>
                          <PlatformBadge code={row.platform_code} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}

            {visibleCount < rows.length && (
              <div className="lb-more">
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => setVisibleCount((current) => current + PAGE_STEP)}
                >
                  Показать ещё
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </PortalSectionShell>
  );
}
