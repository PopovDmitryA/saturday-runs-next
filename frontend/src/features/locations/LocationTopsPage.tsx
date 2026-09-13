/**
 * Топы бегунов локации: полные зачёты по лучшему времени и по числу побед.
 *
 * Устроена как постоянный состав: зачёты приезжают целиком, а фильтры,
 * сортировка и «Показать ещё» работают на клиенте. На самой странице локации
 * от каждого топа видна только пятёрка — оттуда сюда и ведут ссылки.
 *
 * Четыре зачёта, а не два с фильтром по полу: «по времени» мужчины и женщины
 * действительно срез одного списка, а вот победа среди женщин — самостоятельный
 * зачёт. Женщина, ни разу не выигравшая абсолют, в женском может держать
 * первое место, и складывать эти два списка в один нельзя.
 */

import { useEffect, useMemo, useState } from "react";
import { useCachedResource } from "../../hooks/useCachedResource";
import { useRestorableState } from "../../hooks/useRestorableState";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { PlatformBadge } from "../../components/PlatformBadge";
import { ScrollToTopButton } from "../../components/ScrollToTopButton";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import {
  FilterGroup,
  FilterPanel,
  FilterRow,
  FilterSearch,
  FilterTabs,
} from "../../components/filters/FilterPanel";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { TableViewToggle } from "../../components/tableUx/TableViewToggle";
import { useTableColumns } from "../../components/tableUx/useTableColumns";
import type { AdaptiveColumn } from "../../components/tableUx/useAdaptiveColumns";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import {
  getLocationTops,
  type LocationFastestRunner,
  type LocationTopWinner,
} from "../../lib/api";
import { applyPageMeta, locationPageMeta } from "../../lib/pageMeta";
import { flushMetrikaHit } from "../../lib/metrika";
import { formatDate, formatInt, platformCodeLabel } from "../../lib/format";
import { locationHintFor, rememberLocationHint } from "../../lib/locationHint";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import {
  TOP_TIME_HINT,
  WINS_FEMALE_HINT,
  WINS_OVERALL_HINT,
  stripLeadingHours,
} from "./LocationTopCards";

/** Что считаем: лучшее время или победы. */
type Board = "time" | "wins";
/** Зачёт внутри выбранного: мужчины / женщины, абсолют / женщины. */
type Division = "primary" | "female";

type SortKey = "place" | "name" | "value" | "first" | "last" | "system" | "extra";
type SortState = { key: SortKey; asc: boolean };

/** Одна строка таблицы — общая форма для обоих зачётов. */
type TopRow = {
  place: number;
  name: string | null;
  handle?: string | null;
  /** Главное число зачёта: секунды лучшего времени или количество побед. */
  value: number;
  valueDisplay: string;
  /** Системы строки: у времени одна, у побед их может быть две. */
  systems: string[];
  /** Дата, с которой всё началось: первая победа. У времени её нет. */
  firstDate: string | null;
  /** Дата результата: когда показано время или когда была последняя победа. */
  lastDate: string | null;
  /** Второе число: финишей здесь у зачёта по времени. */
  extra: number | null;
};

function fastestRow(row: LocationFastestRunner): TopRow {
  return {
    place: row.place,
    name: row.name,
    handle: row.handle,
    value: row.best_time_sec,
    valueDisplay: stripLeadingHours(row.best_time_display),
    systems: row.platform_codes,
    firstDate: null,
    lastDate: row.event_date,
    extra: row.finishes_count,
  };
}

function winnerRow(row: LocationTopWinner): TopRow {
  return {
    place: row.place,
    name: row.name,
    handle: row.handle,
    value: row.wins_count,
    valueDisplay: formatInt(row.wins_count),
    systems: row.platform_codes,
    firstDate: row.first_win_date,
    lastDate: row.last_win_date,
    extra: null,
  };
}

const BOARD_WORDS: Record<Board, { label: string; divisions: Record<Division, string> }> = {
  time: {
    label: "По времени",
    divisions: { primary: "Мужчины", female: "Женщины" },
  },
  wins: {
    label: "По победам",
    divisions: { primary: "Абсолют", female: "Женщины" },
  },
};

// Колонки в порядке важности — краткий вид набирает столько, сколько влезло.
const TIME_COLUMNS: AdaptiveColumn[] = [
  { key: "place", width: 56, required: true },
  { key: "name", width: 170, required: true },
  { key: "value", width: 96, required: true },
  { key: "system", width: 104 },
  { key: "last", width: 128 },
  { key: "extra", width: 112 },
];

const WINS_COLUMNS: AdaptiveColumn[] = [
  { key: "place", width: 56, required: true },
  { key: "name", width: 170, required: true },
  { key: "value", width: 88, required: true },
  { key: "system", width: 104 },
  { key: "last", width: 128 },
  { key: "first", width: 128 },
];

/** Сколько строк показываем сразу и сколько добавляет «Показать ещё». */
const PAGE_STEP = 100;

/** Зачёт из адреса: со страницы локации ссылка ведёт сразу в нужную таблицу. */
function boardFromLocation(): Board {
  if (typeof window === "undefined") {
    return "time";
  }
  return new URLSearchParams(window.location.search).get("board") === "wins" ? "wins" : "time";
}

function divisionFromLocation(): Division {
  if (typeof window === "undefined") {
    return "primary";
  }
  return new URLSearchParams(window.location.search).get("division") === "female"
    ? "female"
    : "primary";
}

function sortValue(row: TopRow, key: SortKey): number | string | null {
  switch (key) {
    case "place":
      return row.place;
    case "name":
      return row.name ?? "";
    case "value":
      return row.value;
    case "system":
      return row.systems[0] ?? null;
    case "first":
      return row.firstDate;
    case "last":
      return row.lastDate;
    case "extra":
      return row.extra;
  }
}

/** Регистр и «ё» поиску не мешают: «фёдоров» находит «ФЕДОРОВ» и наоборот. */
function normalizeName(value: string): string {
  return value.trim().toLowerCase().replace(/ё/g, "е");
}

function LocationTopsContent({ slug }: { slug: string }) {
  const [initialBoard] = useState(boardFromLocation);
  const [initialDivision] = useState(divisionFromLocation);
  const { data, notFound, error } = useCachedResource(
    `locations:tops:${slug}`,
    () => getLocationTops(slug),
    [slug],
    {
      errorText: "Не удалось загрузить топы",
      onLoaded: (payload) => {
        rememberLocationHint({ slug: payload.slug, name: payload.name });
        applyPageMeta(locationPageMeta(payload, { tops: true }));
      },
      // Просмотр был, пусть и неудачный — досылаем с родовым заголовком.
      onSettled: flushMetrikaHit,
    },
  );
  const [board, setBoard] = useState<Board>(initialBoard);
  const [division, setDivision] = useState<Division>(initialDivision);
  const [sort, setSort] = useRestorableState<SortState>("tops.sort", { key: "place", asc: true });
  const [query, setQuery] = useRestorableState("tops.query", "");
  const [platform, setPlatform] = useRestorableState("tops.platform", "all");
  const [visibleCount, setVisibleCount] = useRestorableState("tops.visible", PAGE_STEP);
  // Копия шапки встаёт под липкую полосу «Кратко | Полно», а не под шапку сайта.
  const attachFloatingHead = useFloatingTableHead(".tview-bar");
  const tableColumns = useTableColumns(board === "time" ? TIME_COLUMNS : WINS_COLUMNS);
  const showFull = tableColumns.showFull;
  const show = tableColumns.show;

  // Зачёт — в адресе: ссылкой на «топ по победам среди женщин» можно делиться.
  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const url = new URL(window.location.href);
    url.searchParams.set("board", board);
    url.searchParams.set("division", division);
    window.history.replaceState(null, "", url.toString());
  }, [board, division]);

  const boardRows = useMemo<TopRow[]>(() => {
    if (!data) {
      return [];
    }
    if (board === "time") {
      const source = division === "female" ? data.fastest_female : data.fastest_male;
      return source.map(fastestRow);
    }
    const source = division === "female" ? data.winners_female : data.winners_overall;
    return source.map(winnerRow);
  }, [data, board, division]);

  const platformRows = useMemo(() => {
    if (platform === "all") {
      return boardRows;
    }
    return boardRows.filter((row) => row.systems.includes(platform));
  }, [boardRows, platform]);

  const sortedRows = useMemo(() => {
    const sorted = [...platformRows];
    sorted.sort((a, b) => {
      const left = sortValue(a, sort.key);
      const right = sortValue(b, sort.key);
      if (left === right) {
        // Равные значения — по месту в зачёте, чтобы порядок не «дрожал».
        return a.place - b.place;
      }
      // null-значения всегда в конец, независимо от направления.
      if (left === null) {
        return 1;
      }
      if (right === null) {
        return -1;
      }
      const compare = left < right ? -1 : 1;
      return sort.asc ? compare : -compare;
    });
    return sorted;
  }, [platformRows, sort]);

  const rows = useMemo(() => {
    const needle = normalizeName(query);
    if (!needle) {
      return sortedRows;
    }
    return sortedRows.filter((row) => normalizeName(row.name ?? "").includes(needle));
  }, [sortedRows, query]);

  // Смена зачёта, системы или запроса начинает список заново.
  useEffect(() => {
    setVisibleCount(PAGE_STEP);
  }, [board, division, platform, query]);

  const shownRows = useMemo(() => rows.slice(0, visibleCount), [rows, visibleCount]);
  const hasMore = rows.length > shownRows.length;

  const toggleSort = (key: SortKey) => {
    setSort((current) =>
      current.key === key
        ? { key, asc: !current.asc }
        : // Имя, место и время интереснее по возрастанию, остальное — сверху вниз.
          { key, asc: key === "place" || key === "name" || (key === "value" && board === "time") },
    );
  };

  const sortProps = (key: SortKey) => ({
    filterable: false,
    sortActive: sort.key === key,
    sortAsc: sort.asc,
    onSort: () => toggleSort(key),
  });

  const columns = board === "time" ? TIME_COLUMNS : WINS_COLUMNS;
  const visibleColumnCount = columns.filter((column) => show(column.key)).length;

  // Пока данные едут, имя берём из подсказки — иначе подпункт сайдбара с
  // названием площадки мигает при каждом переходе внутри локации.
  const sidebarLocation = data ? { slug: data.slug, name: data.name } : locationHintFor(slug);
  const sidebar = { active: "locations" as const, location: sidebarLocation };

  if (notFound) {
    return (
      <PortalSectionShell sidebar={sidebar}>
        <div className="card">
          <p className="muted">Локация не найдена.</p>
          <p>
            <a href="/locations">Все локации</a>
          </p>
        </div>
      </PortalSectionShell>
    );
  }
  if (error) {
    return (
      <PortalSectionShell sidebar={sidebar}>
        <div className="card error">
          <p>{error}</p>
        </div>
      </PortalSectionShell>
    );
  }
  if (!data) {
    return (
      <PortalSectionShell sidebar={sidebar}>
        <p className="muted">Загрузка…</p>
      </PortalSectionShell>
    );
  }

  const words = BOARD_WORDS[board];
  const platformOptions = data.platform_codes ?? [];
  const empty = boardRows.length === 0;
  // Счётчики в переключателе зачёта — сразу видно, где сколько строк.
  const boardSize = (value: Board, scope: Division): number => {
    if (value === "time") {
      return (scope === "female" ? data.fastest_female : data.fastest_male).length;
    }
    return (scope === "female" ? data.winners_female : data.winners_overall).length;
  };

  return (
    <PortalSectionShell sidebar={sidebar}>
      <header className="loc-header loc-wide-page">
        <p className="muted loc-header-breadcrumb">
          <a href="/locations">← Все локации</a> /{" "}
          <a href={`/locations/${data.slug}`}>{data.name}</a> / Топы бегунов
        </p>
        <div className="loc-header-title">
          <h1>{data.name} — топы бегунов</h1>
          <span className="muted loc-people-lead">
            {board === "time"
              ? "Лучшее время каждого, кто здесь финишировал."
              : "Кто и сколько раз финишировал здесь первым."}
          </span>
        </div>
      </header>

      <FilterPanel>
        <FilterRow>
          <FilterGroup label="Топ">
            <FilterTabs
              asTablist
              ariaLabel="Топ"
              value={board}
              onChange={(value) => setBoard(value as Board)}
              options={(["time", "wins"] as Board[]).map((key) => ({
                value: key,
                label: BOARD_WORDS[key].label,
              }))}
            />
          </FilterGroup>
          <FilterGroup label="Зачёт">
            <FilterTabs
              asTablist
              ariaLabel="Зачёт"
              value={division}
              onChange={(value) => setDivision(value as Division)}
              options={(["primary", "female"] as Division[]).map((key) => ({
                value: key,
                label: `${words.divisions[key]} (${formatInt(boardSize(board, key))})`,
              }))}
            />
          </FilterGroup>
          {platformOptions.length > 1 && (
            <FilterGroup label="Система">
              <FilterTabs
                asTablist
                ariaLabel="Система"
                value={platform}
                onChange={setPlatform}
                options={[
                  { value: "all", label: "Все" },
                  ...platformOptions.map((code) => ({
                    value: code,
                    label: platformCodeLabel(code),
                  })),
                ]}
              />
            </FilterGroup>
          )}
          {tableColumns.hasToggle && (
            <FilterGroup label="Колонки">
              <TableViewToggle columns={tableColumns} inline />
            </FilterGroup>
          )}
          <FilterGroup label="Поиск" trailing>
            <FilterSearch
              value={query}
              onChange={setQuery}
              ariaLabel="Поиск по имени или фамилии"
            />
          </FilterGroup>
        </FilterRow>
        {query.trim() && (
          <span className="muted loc-people-found">
            {rows.length > 0 ? `Найдено: ${formatInt(rows.length)}` : "Никого не нашли"}
          </span>
        )}
      </FilterPanel>

      <section className="loc-section">
        <TableWrap
          innerRef={attachFloatingHead}
          outerRef={tableColumns.measureRef}
          className="loc-events-wrap"
          stickyFirstCol={showFull}
        >
          <table
            className={`data-table data-table-layout-fixed loc-people-table${
              showFull ? "" : " data-table-short"
            }`}
            style={{ minWidth: tableColumns.minWidth }}
          >
            <colgroup>
              <col className="col-place" />
              <col className="col-name" />
              <col className="col-count" />
              {show("system") && <col className="col-count" />}
              {show("last") && <col className="col-date" />}
              {show(board === "time" ? "extra" : "first") && (
                <col className={board === "time" ? "col-count" : "col-date"} />
              )}
            </colgroup>
            <thead>
              <tr>
                <ColumnHeader
                  label="#"
                  headerTitle="Место в зачёте локации: равный результат — равное место. В срезе по системе номера идут с пропусками — это места в общем зачёте"
                  {...sortProps("place")}
                />
                <ColumnHeader label="Участник" {...sortProps("name")} />
                <ColumnHeader
                  label={board === "time" ? "Время" : "Побед"}
                  hint={
                    board === "time"
                      ? "Лучшее время участника здесь"
                      : "Сколько раз финишировал здесь первым"
                  }
                  {...sortProps("value")}
                />
                {show("system") && (
                  <ColumnHeader
                    label="Система"
                    hint={
                      board === "time"
                        ? "Системы, чьи протоколы учтены в строке"
                        : "Системы, в протоколах которых засчитаны победы"
                    }
                    {...sortProps("system")}
                  />
                )}
                {show("last") && (
                  <ColumnHeader
                    label={board === "time" ? "Дата" : "Последняя"}
                    hint={
                      board === "time" ? "Когда показано это время" : "Дата последней победы"
                    }
                    {...sortProps("last")}
                  />
                )}
                {board === "time" && show("extra") && (
                  <ColumnHeader
                    label="Финишей здесь"
                    hint="Сколько раз человек финишировал на этой локации"
                    {...sortProps("extra")}
                  />
                )}
                {board === "wins" && show("first") && (
                  <ColumnHeader
                    label="Первая"
                    hint="Дата первой победы"
                    {...sortProps("first")}
                  />
                )}
              </tr>
            </thead>
            <tbody>
              {shownRows.length === 0 ? (
                <tr>
                  <td colSpan={visibleColumnCount} className="table-empty-cell">
                    <span className="muted">
                      {query.trim()
                        ? "Никого не нашли — попробуйте другую часть имени"
                        : empty
                          ? "По этой площадке зачётов нет: её протоколы мы собираем не целиком"
                          : "В этом срезе никого нет"}
                    </span>
                  </td>
                </tr>
              ) : (
                // Ключ по индексу намеренно: строки не несут своего состояния,
                // а имя с местом уникальными не бывают — у человека с
                // непривязанными аккаунтами в разных системах строки совпадают.
                shownRows.map((row, index) => (
                  <tr key={`${board}-${division}-${index}`}>
                    <td className="td-compact loc-people-place">{row.place}</td>
                    <td>
                      <PersonName name={row.name} handle={row.handle} />
                    </td>
                    <td className="td-compact">{row.valueDisplay}</td>
                    {show("system") && (
                      <td>
                        {row.systems.length === 0 ? (
                          "—"
                        ) : (
                          <div className="loc-top-systems">
                            {row.systems.map((code) => (
                              <PlatformBadge key={code} code={code} />
                            ))}
                          </div>
                        )}
                      </td>
                    )}
                    {show("last") && <td className="td-date">{formatDay(row.lastDate)}</td>}
                    {board === "time" && show("extra") && (
                      <td className="td-compact">{row.extra === null ? "—" : formatInt(row.extra)}</td>
                    )}
                    {board === "wins" && show("first") && (
                      <td className="td-date">{formatDay(row.firstDate)}</td>
                    )}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </TableWrap>
        {hasMore && (
          <div className="loc-people-more">
            <button
              type="button"
              className="btn"
              onClick={() => setVisibleCount((current) => current + PAGE_STEP)}
            >
              Показать ещё (места {visibleCount + 1}–
              {Math.min(visibleCount + PAGE_STEP, rows.length)})
            </button>
          </div>
        )}
        <p className="table-foot muted">
          {rows.length > 0 && (
            <>
              Показано {formatInt(shownRows.length)} из {formatInt(rows.length)}.{" "}
            </>
          )}
          <StatHintTooltip
            text={
              board === "time"
                ? TOP_TIME_HINT
                : division === "female"
                  ? WINS_FEMALE_HINT
                  : WINS_OVERALL_HINT
            }
          >
            <span className="loc-section-title-info" aria-label="Как считается">
              ⓘ
            </span>
          </StatHintTooltip>{" "}
          Как считается
        </p>
        <p className="loc-leaders-more">
          <a className="loc-events-link" href={`/locations/${data.slug}/participants`}>
            Постоянный состав локации →
          </a>
        </p>
      </section>
      <ScrollToTopButton />
    </PortalSectionShell>
  );
}

/** Имя со ссылкой на публичный профиль, если он у человека есть. */
function PersonName({ name, handle }: { name: string | null; handle?: string | null }) {
  const label = name?.trim() || "—";
  if (!handle || label === "—") {
    return <>{label}</>;
  }
  return (
    <a className="loc-runner-link" href={`/users/${encodeURIComponent(handle)}`}>
      {label}
    </a>
  );
}

/** Дата или прочерк. */
function formatDay(value: string | null): string {
  return value ? formatDate(value) : "—";
}

// Топы бегунов открыты без логина, как и вся витрина локаций.
export function LocationTopsPage({ slug }: { slug: string }) {
  return <LocationTopsContent slug={slug} />;
}
