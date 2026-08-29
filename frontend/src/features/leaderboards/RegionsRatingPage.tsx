import { useCallback, useEffect, useMemo, useState, useRef } from "react";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { useNarrowViewport } from "../../components/tableUx/useNarrowViewport";
import { formatInt, pluralFormRu, pluralizeRu } from "../../lib/format";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { RatingsLoginBanner } from "./RatingsLoginBanner";
import {
  REGIONS_PLATFORM_LABELS,
  getRegionsRating,
  type RegionRatingRow,
  type RegionsPlatform,
  type RegionsRatingResponse,
} from "./regionsApi";
import "./leaderboards.css";

// Системы, чьи колонки показываем в общем зачёте. Порядок — как в остальных
// витринах: 5 вёрст, С95, RunPark.
const PLATFORM_COLUMNS = ["five_verst", "s95", "runpark"] as const;

const COUNT_HINT =
  "Считаем действующие площадки: парк, который живёт сразу в двух системах, — одна " +
  "локация региона. Поэтому сумма по колонкам систем бывает больше общего числа. " +
  "Площадки со статусом «не действует» в счёт не идут и показаны подписью.";

const FOREIGN_HINT =
  "Зарубежные площадки считаются по странам, а не по регионам — как на карте, где " +
  "Россия раскрашена по регионам, а остальной мир по странам.";

function isPlatform(value: string): value is RegionsPlatform {
  return value === "all" || value === "five_verst" || value === "s95" || value === "runpark";
}

function matchesQuery(row: RegionRatingRow, query: string): boolean {
  const needle = query.trim().toLowerCase();
  return !needle || row.name.toLowerCase().includes(needle);
}

/** Полоса под числом: рейтинг читается взглядом, а не сложением столбцов. */
function CountBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.max(4, Math.round((value / max) * 100)) : 0;
  return (
    <span className="lb-regions-bar" aria-hidden>
      <span className="lb-regions-bar-fill" style={{ width: `${pct}%` }} />
    </span>
  );
}

function PausedNote({ paused }: { paused: number }) {
  if (paused <= 0) {
    return null;
  }
  return <div className="muted lb-regions-note">+{paused} не действует</div>;
}

/** Разбивка по системам одной строкой — для карточек узкого экрана. */
function platformSummary(row: RegionRatingRow): string {
  return PLATFORM_COLUMNS.filter((code) => (row.by_platform[code] ?? 0) > 0)
    .map((code) => `${REGIONS_PLATFORM_LABELS[code]} ${row.by_platform[code]}`)
    .join(" · ");
}

// Сортировка по столбцам. Ключ «name» — алфавит области, остальные — числовые
// колонки, включая счётчики систем.
type SortKey = "name" | "locations" | "cities" | (typeof PLATFORM_COLUMNS)[number];

type SortState = { key: SortKey; direction: "asc" | "desc" };

/** Первый клик по столбцу: у чисел интересен максимум, у названия — алфавит. */
function defaultDirection(key: SortKey): SortState["direction"] {
  return key === "name" ? "asc" : "desc";
}

function sortValue(row: RegionRatingRow, key: SortKey): number {
  if (key === "locations") {
    return row.locations;
  }
  if (key === "cities") {
    return row.cities;
  }
  return row.by_platform[key] ?? 0;
}

function compareRows(left: RegionRatingRow, right: RegionRatingRow, sort: SortState): number {
  const factor = sort.direction === "asc" ? 1 : -1;
  if (sort.key === "name") {
    return left.name.localeCompare(right.name, "ru") * factor;
  }
  const delta = sortValue(left, sort.key) - sortValue(right, sort.key);
  // При равных числах порядок — по месту в рейтинге: иначе строки с одним и тем
  // же счётчиком (а их десятки) прыгали бы при каждой пересортировке.
  return delta !== 0 ? delta * factor : left.place - right.place;
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
  return (
    <th
      className={`${className ?? ""} lb-sortable${active ? " lb-sorted" : ""}`}
      onClick={() => onSort(sortKey)}
      title="Сортировать по этому столбцу"
      aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}
      // Тап по заголовку сортирует — подсказку тач-режима здесь не показываем.
      data-tap-tooltip="off"
    >
      {label}
      <span className="lb-sort-mark" aria-hidden>
        {active ? (sort.direction === "asc" ? "▴" : "▾") : "↕"}
      </span>
    </th>
  );
}

function RegionsTable({
  rows,
  showPlatforms,
  areaLabel,
}: {
  rows: RegionRatingRow[];
  showPlatforms: boolean;
  areaLabel: string;
}) {
  const narrowViewport = useNarrowViewport();
  const attachFloatingHead = useFloatingTableHead();
  // Таблица приходит отсортированной по числу локаций — это и есть место в
  // рейтинге. Остальные столбцы читают иначе: где больше городов, где сильнее
  // одна система.
  const [sort, setSort] = useState<SortState>({ key: "locations", direction: "desc" });
  const max = rows.length > 0 ? Math.max(...rows.map((row) => row.locations)) : 0;
  const sortedRows = useMemo(
    () => [...rows].sort((left, right) => compareRows(left, right, sort)),
    [rows, sort],
  );
  // Повторный клик по тому же столбцу переворачивает порядок, по новому —
  // ставит осмысленное направление по умолчанию.
  const toggleSort = useCallback((key: SortKey) => {
    setSort((current) =>
      current.key === key
        ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
        : { key, direction: defaultDirection(key) },
    );
  }, []);

  if (narrowViewport) {
    return (
      <div className="rowcards">
        {sortedRows.map((row) => (
          <div className="rowcard" key={row.name}>
            <div className="rowcard-rank">{row.place}</div>
            <div className="rowcard-mid">
              <div className="rowcard-title">{row.name}</div>
              <div className="rowcard-sub">
                {showPlatforms && platformSummary(row)}
                {showPlatforms && row.cities > 0 && " · "}
                {row.cities > 0 && pluralizeRu(row.cities, ["город", "города", "городов"])}
              </div>
            </div>
            <div className="rowcard-right">
              <div className="rowcard-value">{formatInt(row.locations)}</div>
              <PausedNote paused={row.paused} />
            </div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <TableWrap className="lb-table-wrap lb-table-wrap-flat" innerRef={attachFloatingHead}>
      <table className="data-table lb-table lb-regions-table">
        <thead>
          <tr>
            <th>#</th>
            <SortableHeader label={areaLabel} sortKey="name" sort={sort} onSort={toggleSort} />
            <SortableHeader
              label="Локаций"
              sortKey="locations"
              sort={sort}
              onSort={toggleSort}
              className="lb-regions-count"
            />
            {showPlatforms &&
              PLATFORM_COLUMNS.map((code) => (
                <SortableHeader
                  key={code}
                  label={REGIONS_PLATFORM_LABELS[code]}
                  sortKey={code}
                  sort={sort}
                  onSort={toggleSort}
                  className="lb-regions-platform"
                />
              ))}
            <SortableHeader
              label="Городов"
              sortKey="cities"
              sort={sort}
              onSort={toggleSort}
              className="lb-regions-cities"
            />
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row) => (
            <tr key={row.name}>
              <td className="lb-regions-rank">{row.place}</td>
              <td>{row.name}</td>
              <td className="lb-regions-count">
                <span className="lb-regions-count-value">{formatInt(row.locations)}</span>
                <CountBar value={row.locations} max={max} />
                <PausedNote paused={row.paused} />
              </td>
              {showPlatforms &&
                PLATFORM_COLUMNS.map((code) => (
                  <td key={code} className="lb-regions-platform">
                    {row.by_platform[code] ? formatInt(row.by_platform[code]) : "—"}
                  </td>
                ))}
              <td className="lb-regions-cities">{row.cities > 0 ? formatInt(row.cities) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

export function RegionsRatingPage() {
  const [data, setData] = useState<RegionsRatingResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  // Стартовый фильтр — из ссылки: рейтинг одной системы кидают в чат, и адрес
  // обязан открывать ровно то, что человек видел.
  const initialPlatform = useMemo<RegionsPlatform>(() => {
    const value = new URLSearchParams(window.location.search).get("platform") ?? "all";
    return isPlatform(value) ? value : "all";
  }, []);
  const [platform, setPlatform] = useState<RegionsPlatform>(initialPlatform);

  // requestId отсекает устаревшие ответы: без него медленный ответ прежней
  // системы перезаписывал бы только что показанную новую (и URL за ним).
  const requestIdRef = useRef(0);

  const load = useCallback(async () => {
    const requestId = ++requestIdRef.current;
    setLoading(true);
    setError(null);
    try {
      const payload = await getRegionsRating(platform);
      if (requestId === requestIdRef.current) {
        setData(payload);
      }
    } catch (loadError) {
      if (requestId === requestIdRef.current) {
        setError(loadError instanceof Error ? loadError.message : "Не удалось загрузить рейтинг");
      }
    } finally {
      if (requestId === requestIdRef.current) {
        setLoading(false);
      }
    }
  }, [platform]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!data) {
      return;
    }
    const url = new URL(window.location.href);
    if (data.platform === "all") {
      url.searchParams.delete("platform");
    } else {
      url.searchParams.set("platform", data.platform);
    }
    window.history.replaceState(null, "", url.toString());
  }, [data]);

  const regions = useMemo(
    () => (data?.regions ?? []).filter((row) => matchesQuery(row, query)),
    [data, query],
  );
  const countries = useMemo(
    () => (data?.countries ?? []).filter((row) => matchesQuery(row, query)),
    [data, query],
  );

  // Колонки систем нужны только в общем зачёте: при выбранной системе это одна
  // колонка, повторяющая «Локаций».
  const showPlatforms = data?.platform === "all";

  return (
    <PortalSectionShell sidebar={{ active: "ratings" }}>
      <div className="lb-page">
        <nav className="lb-breadcrumb">
          <a href="/ratings">← Все рейтинги</a>
          <span aria-hidden> / </span>
          <span>Локации · Локации по регионам</span>
        </nav>

        <header className="lb-header">
          <h1>Локации по регионам</h1>
          <p className="lb-description">
            Где субботних пятёрок больше всего: 5 вёрст, С95 и RunPark в одной таблице.
            Одна площадка — одна строка счёта, даже если она живёт сразу в двух системах.
            Зарубежные старты идут отдельно, по странам.
          </p>
        </header>

        <RatingsLoginBanner />

        <div className="lb-controls-row lb-locrec-controls">
          <div className="lb-controls-left">
            <div className="lb-visits">
              <span className="lb-visits-label">
                Система{" "}
                <StatHintTooltip text={COUNT_HINT}>
                  <span aria-label="Как считается">ⓘ</span>
                </StatHintTooltip>
              </span>
              <div className="lb-gender-tabs" role="group" aria-label="Система">
                {(data?.platforms ?? ["all", ...PLATFORM_COLUMNS]).map((value) => (
                  <button
                    key={value}
                    type="button"
                    aria-pressed={platform === value}
                    className={`lb-gender-tab${platform === value ? " lb-gender-tab-active" : ""}`}
                    onClick={() => isPlatform(value) && setPlatform(value)}
                  >
                    {REGIONS_PLATFORM_LABELS[value] ?? value}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <div className="lb-controls-right">
            <input
              className="lb-search lb-locrec-search"
              type="search"
              placeholder="Поиск по региону или стране…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
        </div>

        {loading && !data && <p className="muted">Считаем рейтинг…</p>}
        {error && (
          <div className="lb-error">
            <p>{error}</p>
            <button type="button" className="btn btn-sm" onClick={() => void load()}>
              Повторить
            </button>
          </div>
        )}

        {data && (
          <div className={`lb-page-body${loading ? " lb-refreshing" : ""}`}>
            <div className="lb-regions-totals">
              <div className="lb-regions-total">
                <span className="lb-regions-total-value">{formatInt(data.totals.regions)}</span>
                <span className="lb-regions-total-label">
                  {pluralFormRu(data.totals.regions, ["регион", "региона", "регионов"])} России
                </span>
              </div>
              <div className="lb-regions-total">
                <span className="lb-regions-total-value">
                  {formatInt(data.totals.region_locations)}
                </span>
                <span className="lb-regions-total-label">локаций в них</span>
              </div>
              {data.totals.countries > 0 && (
                <div className="lb-regions-total">
                  <span className="lb-regions-total-value">
                    {formatInt(data.totals.country_locations)}
                  </span>
                  <span className="lb-regions-total-label">
                    за рубежом, в{" "}
                    {pluralizeRu(data.totals.countries, ["стране", "странах", "странах"])}
                  </span>
                </div>
              )}
            </div>

            {regions.length === 0 ? (
              <p className="muted">Ничего не нашлось — попробуйте другой запрос.</p>
            ) : (
              <RegionsTable rows={regions} showPlatforms={showPlatforms} areaLabel="Регион" />
            )}

            {countries.length > 0 && (
              <section className="lb-regions-foreign">
                <h2>
                  Зарубежные площадки{" "}
                  <StatHintTooltip text={FOREIGN_HINT}>
                    <span aria-label="Как считается">ⓘ</span>
                  </StatHintTooltip>
                </h2>
                <RegionsTable
                  rows={countries}
                  showPlatforms={showPlatforms}
                  areaLabel="Страна"
                />
              </section>
            )}

            {data.totals.unknown_region > 0 && (
              <p className="muted lb-regions-note">
                Ещё {pluralizeRu(data.totals.unknown_region, ["локация", "локации", "локаций"])} без
                региона — геокод до них пока не дошёл.
              </p>
            )}
          </div>
        )}
      </div>
    </PortalSectionShell>
  );
}
