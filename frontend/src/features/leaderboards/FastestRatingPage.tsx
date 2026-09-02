import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FilterSelect } from "../../components/filters/FilterPanel";
import { GenderFilter } from "../../components/filters/GenderFilter";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { PlatformBadge } from "../../components/PlatformBadge";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { PinnedMeBar } from "../../components/tableUx/PinnedMeBar";
import { TableViewToggle } from "../../components/tableUx/TableViewToggle";
import { TableWrap } from "../../components/tableUx/TableWrap";
import type { AdaptiveColumn } from "../../components/tableUx/useAdaptiveColumns";
import { useTableColumns } from "../../components/tableUx/useTableColumns";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { COUNT_FORMS, formatInt, pluralizeRu } from "../../lib/format";
import { useOptionalUser } from "../../lib/useOptionalUser";
import { formatFinishTime } from "./formatFinishTime";
import {
  AGE_GROUP_ALL,
  AGE_GROUP_PLATFORM,
  DEFAULT_FASTEST_GENDER,
  FASTEST_GENDER_TABS,
  FASTEST_MODE_LABELS,
  YEAR_ALL,
  getFastestRating,
  getMyFastestRow,
  isAbort,
  type FastestFilters,
  type FastestGender,
  type FastestMode,
  type FastestRatingResponse,
  type FastestRow,
  type MyFastestRow,
} from "./fastestApi";
import { RatingsLoginBanner } from "./RatingsLoginBanner";
import "./leaderboards.css";

const PAGE_STEP = 100;

const MODE_TABS: FastestMode[] = ["results", "runners"];

const MODE_HINT =
  "«Финиши» — таблица забегов: один человек занимает столько строк, сколько " +
  "быстрых финишей у него есть. «Участники» — по одной строке на человека, его " +
  "лучший результат.";

// Подсказки говорят только то, чего по самой кнопке не видно. Что топ
// пересчитывается под выбранный пункт — общее правило всех фильтров рейтинга,
// и повторять его в каждой подсказке незачем (решение Дмитрия 25.08.2026).
const GENDER_HINT =
  "Строки, где система не назвала пол финишёра, в мужской и женский зачёт не попадают.";

const PLATFORM_HINT = "Зарубежные забеги parkrun в рейтинг не идут.";

// Что делать, когда селектор заперт: подсказка должна не только объяснять, но и
// показывать выход. Висит и на значке «i», и на самом селекторе — на запертый
// select наведение мышью в браузерах отрабатывает через раз, поэтому title
// стоит на обёртке (её же подхватывает общий слой тап-подсказок сайта).
const AGE_LOCKED_HINT =
  "Возрастные группы есть только у 5 вёрст — выберите эту систему.";

const AGE_HINT =
  "Возрастную группу забега публикуют не все системы, поэтому возрастной зачёт " +
  "считается только по 5 вёрст. Группа берётся из протокола, то есть та, в " +
  "которой человек бежал в тот день.";

function InfoHint({ text }: { text: string }) {
  return (
    <StatHintTooltip text={text} className="lb-info-hint">
      <span aria-hidden="true">i</span>
    </StatHintTooltip>
  );
}

function formatDate(value: string | null): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("ru-RU");
}

/** Фильтры живут в адресе: ссылкой на конкретный срез делятся в чатах локаций. */
function readFilters(): FastestFilters {
  const params = new URLSearchParams(window.location.search);
  const mode = params.get("mode") === "runners" ? "runners" : "results";
  const gender = params.get("gender");
  return {
    mode,
    platform: params.get("platform") ?? "all",
    gender:
      gender === "male" || gender === "female" || gender === "all"
        ? gender
        : DEFAULT_FASTEST_GENDER,
    ageGroup: params.get("age_group") ?? AGE_GROUP_ALL,
    year: params.get("year") ?? YEAR_ALL,
  };
}

function writeFilters(filters: FastestFilters): void {
  const params = new URLSearchParams();
  if (filters.mode !== "results") {
    params.set("mode", filters.mode);
  }
  if (filters.platform !== "all") {
    params.set("platform", filters.platform);
  }
  params.set("gender", filters.gender);
  if (filters.ageGroup !== AGE_GROUP_ALL) {
    params.set("age_group", filters.ageGroup);
  }
  if (filters.year !== YEAR_ALL) {
    params.set("year", filters.year);
  }
  const query = params.toString();
  window.history.replaceState(null, "", query ? `?${query}` : window.location.pathname);
}

function ParticipantName({ row }: { row: FastestRow }) {
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

function LocationCell({ row }: { row: FastestRow }) {
  if (!row.location_name) {
    return <span className="lb-zero">—</span>;
  }
  if (!row.location_slug) {
    return <span className="lb-fastest-location">{row.location_name}</span>;
  }
  return (
    <a className="lb-last-win-link" href={`/locations/${row.location_slug}`}>
      {row.location_name}
    </a>
  );
}

/** Дата ведёт в наш протокол этого старта — там видно, кого он тогда обогнал. */
function DateCell({ row }: { row: FastestRow }) {
  const label = formatDate(row.event_date);
  if (!row.protocol_url) {
    return <span className="lb-last-win-date">{label}</span>;
  }
  return (
    <a className="lb-last-win-link" href={row.protocol_url}>
      {label}
    </a>
  );
}

export function FastestRatingPage() {
  const [filters, setFilters] = useState<FastestFilters>(readFilters);
  const [data, setData] = useState<FastestRatingResponse | null>(null);
  const [me, setMe] = useState<MyFastestRow | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [visibleCount, setVisibleCount] = useState(PAGE_STEP);
  // Поиск — единственный фильтр страницы, который НЕ пересчитывает рейтинг:
  // он просеивает уже полученные строки, а места в них остаются глобальными.
  // Так и задуман вопрос «сколько раз наш человек попал в этот топ» и «сколько
  // в нём бегунов с нашей локации» — ответ на него даёт счётчик под полем.
  const [query, setQuery] = useState("");
  const currentUser = useOptionalUser();

  const tableRef = useRef<HTMLTableElement | null>(null);
  const myRowRef = useRef<HTMLTableRowElement | null>(null);
  const attachFloatingHead = useFloatingTableHead(".tview-bar");

  /**
   * Система — главный фильтр, остальные под неё подстраиваются.
   *
   * Так сделано, чтобы страница не «сбрасывала» выбор молча: раньше выбор
   * возрастной группы сам переставлял систему на 5 вёрст, а parkrun спокойно
   * сочетался с 2026 годом и давал пустую таблицу. Теперь невозможное просто
   * не предлагается — группы доступны только у 5 вёрст (только они печатают
   * диапазон возраста), а список лет всегда от выбранной системы.
   */
  const patch = useCallback(
    (next: Partial<FastestFilters>, yearsByPlatform: Record<string, number[]>) => {
      setFilters((current) => {
        const merged = { ...current, ...next };
        if (merged.platform !== AGE_GROUP_PLATFORM) {
          merged.ageGroup = AGE_GROUP_ALL;
        }
        const years = yearsByPlatform[merged.platform] ?? [];
        if (merged.year !== YEAR_ALL && !years.includes(Number(merged.year))) {
          merged.year = YEAR_ALL;
        }
        writeFilters(merged);
        return merged;
      });
    },
    [],
  );

  /**
   * Загрузка среза. Прежний запрос обрывается: каждый срез считается заново, и
   * без отмены три быстрых клика по фильтрам ставили серверу три полных
   * пересчёта в очередь, а таблица оставалась притушённой, пока не ответит
   * последний из них. Строку «Вы» запрашиваем ПОСЛЕ таблицы, а не параллельно —
   * она стоит примерно столько же, и вдвоём они душат друг друга.
   */
  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);
      // Прошлое место рядом со свежей таблицей путает: зачёты финишей и
      // участников дают человеку РАЗНЫЕ места, и старое число секунду-другую
      // стояло под новой таблицей как своё. Прячем до ответа.
      setMe(null);
      try {
        const payload = await getFastestRating(filters, undefined, signal);
        setData(payload);
        setLoading(false);
        if (currentUser) {
          setMe(await getMyFastestRow(filters, signal));
        } else {
          setMe(null);
        }
      } catch (loadError) {
        if (isAbort(loadError)) {
          return;
        }
        setError(
          loadError instanceof Error && loadError.message !== "unauthorized"
            ? loadError.message
            : "Не удалось загрузить рейтинг",
        );
        setLoading(false);
      }
    },
    [currentUser, filters],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    setVisibleCount(PAGE_STEP);
  }, [filters, query]);

  const allRows = data?.rows ?? [];
  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) {
      return allRows;
    }
    // Ищем и по человеку, и по локации одним полем: два отдельных поиска над
    // одной таблицей только множат органы управления.
    return allRows.filter(
      (row) =>
        (row.display_name ?? "").toLowerCase().includes(needle) ||
        (row.location_name ?? "").toLowerCase().includes(needle),
    );
  }, [allRows, query]);
  const searching = query.trim().length > 0;
  const myRowKey = me?.row?.row_key ?? null;
  const myIndex = useMemo(
    () => (myRowKey ? rows.findIndex((row) => row.row_key === myRowKey) : -1),
    [rows, myRowKey],
  );

  const showMyRow = useCallback(() => {
    if (myIndex < 0) {
      return;
    }
    setVisibleCount((count) =>
      myIndex >= count ? Math.ceil((myIndex + 1) / PAGE_STEP) * PAGE_STEP : count,
    );
    window.requestAnimationFrame(() => {
      myRowRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }, [myIndex]);

  // Ширины обязаны совпадать с CSS (.lb-fastest-table .lb-col-*): по ним
  // краткий вид решает, сколько колонок влезает в текущую ширину экрана.
  const columns = useMemo<AdaptiveColumn[]>(
    () => [
      { key: "rank", width: 99, required: true },
      { key: "name", width: 220, required: true },
      { key: "time", width: 88, required: true },
      { key: "location", width: 190 },
      { key: "date", width: 112 },
      { key: "platform", width: 112 },
      { key: "age", width: 96 },
    ],
    [],
  );
  const tableColumns = useTableColumns(columns);
  const show = tableColumns.show;

  const platformLabels = data?.platform_labels ?? {};
  const yearsByPlatform = data?.year_options_by_platform ?? {};
  const setFilter = useCallback(
    (next: Partial<FastestFilters>) => patch(next, yearsByPlatform),
    [patch, yearsByPlatform],
  );
  // Возрастные группы доступны только у 5 вёрст — только они печатают диапазон
  // возраста в протоколе. При любой другой системе селектор заперт и объясняет
  // это подписью, а не молча подменяет систему, как было раньше.
  const ageGroupsAvailable = filters.platform === AGE_GROUP_PLATFORM;
  const yearOptions = yearsByPlatform[filters.platform] ?? [];
  const visibleRows = rows.slice(0, visibleCount);
  const nextChunkEnd = Math.min(visibleCount + PAGE_STEP, rows.length);

  return (
    <PortalSectionShell sidebar={{ active: "ratings" }}>
      <div className="lb-page">
        <nav className="lb-breadcrumb">
          <a href="/ratings">← Все рейтинги</a>
          <span aria-hidden> / </span>
          <span>🏃 Бегуны · Самые быстрые</span>
        </nav>

        {loading && !data && (
          <p className="muted">Считаем рейтинг… Первый расчёт может занять до минуты.</p>
        )}
        {error && !data && (
          <div className="lb-error">
            <p>{error}</p>
            <button type="button" className="btn btn-sm" onClick={() => void load()}>
              Повторить
            </button>
          </div>
        )}

        {data && (
          <div className={`lb-page-body${loading ? " lb-refreshing" : ""}`}>
            <header className="lb-header">
              <h1>{data.title}</h1>
              <p className="lb-description">{data.description}</p>
            </header>

            <RatingsLoginBanner />

            {/* Два ряда — намеренно, а не по остаточному переносу: сверху
                переключатели, снизу выпадающие списки и поиск. Одна строка на
                всё не помещалась ни на каком мониторе, и поиск оставался
                висеть в одиночестве справа. */}
            <div className="lb-fastest-controls">
              <div className="lb-fastest-controls-row">
                {/* У переключателя зачёта есть подпись, как у остальных
                    фильтров: без неё он выпадал из общего ряда и читался как
                    что-то отдельное, не связанное с фильтрами под ним. */}
                <div className="lb-visits">
                  <span className="lb-visits-label">
                    Что считаем <InfoHint text={MODE_HINT} />
                  </span>
                  <div className="lb-gender-tabs" role="tablist" aria-label="Что считаем">
                    {MODE_TABS.map((mode) => (
                      <button
                        key={mode}
                        type="button"
                        role="tab"
                        aria-selected={filters.mode === mode}
                        className={`lb-gender-tab${
                          filters.mode === mode ? " lb-gender-tab-active" : ""
                        }`}
                        onClick={() => setFilter({ mode })}
                      >
                        {FASTEST_MODE_LABELS[mode]}
                      </button>
                    ))}
                  </div>
                </div>

                <GenderFilter
                  label="Зачёт"
                  hint={<InfoHint text={GENDER_HINT} />}
                  value={filters.gender as "male" | "female"}
                  onChange={(next) => setFilter({ gender: next as FastestGender })}
                  options={FASTEST_GENDER_TABS.map((tab) => ({ value: tab.value }))}
                />

                <div className="lb-visits">
                  <span className="lb-visits-label">Год</span>
                  <FilterSelect
                    ariaLabel="Год"
                    value={filters.year}
                    onChange={(value) => setFilter({ year: String(value) })}
                    options={[
                      { value: YEAR_ALL, label: "За всё время" },
                      ...yearOptions.map((year) => ({ value: String(year), label: String(year) })),
                    ]}
                  />
                </div>
              </div>

              <div className="lb-fastest-controls-row">
                <div className="lb-visits">
                  <span className="lb-visits-label">
                    Система <InfoHint text={PLATFORM_HINT} />
                  </span>
                  <div className="lb-gender-tabs" role="group" aria-label="Смотреть по системе">
                    {data.platform_options.map((value) => (
                      <button
                        key={value}
                        type="button"
                        aria-pressed={filters.platform === value}
                        className={`lb-gender-tab${
                          filters.platform === value ? " lb-gender-tab-active" : ""
                        }`}
                        onClick={() => setFilter({ platform: value })}
                      >
                        {value === "all" ? "Все" : (platformLabels[value] ?? value)}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="lb-visits">
                  <span className="lb-visits-label">
                    Группа <InfoHint text={ageGroupsAvailable ? AGE_HINT : AGE_LOCKED_HINT} />
                  </span>
                  <FilterSelect
                    ariaLabel="Возрастная группа"
                    value={filters.ageGroup}
                    disabled={!ageGroupsAvailable}
                    title={ageGroupsAvailable ? undefined : AGE_LOCKED_HINT}
                    onChange={(value) => setFilter({ ageGroup: String(value) })}
                    options={[
                      { value: AGE_GROUP_ALL, label: "Все группы" },
                      ...data.age_group_options.map((group) => ({ value: group, label: group })),
                    ]}
                  />
                </div>

                {tableColumns.hasToggle && (
                  <div className="lb-visits">
                    <span className="lb-visits-label">Колонки</span>
                    <TableViewToggle columns={tableColumns} inline />
                  </div>
                )}
                <div className="lb-visits lb-fastest-search">
                  <span className="lb-visits-label">Поиск в таблице</span>
                  <input
                    className="lb-search"
                    type="search"
                    placeholder="Имя или локация…"
                    aria-label="Поиск по имени или локации"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                  />
                </div>
              </div>
            </div>

            {/* Счётчик найденного и есть ответ на вопросы «сколько раз он попал
                в этот топ» и «сколько в нём наших с локации» — без него поиск
                заставлял бы считать строки глазами. */}
            {searching && (
              <p className="lb-meta muted">
                {rows.length > 0
                  ? // Подписи считаем по data.mode, а не по filters.mode: пока
                    // новый зачёт считается, на экране ещё прежняя таблица, и
                    // фильтр успевал переключить слово «финишей» на
                    // «участников» под нетронутыми строками.
                    `Нашлось ${pluralizeRu(
                      rows.length,
                      data.mode === "runners" ? COUNT_FORMS.participants : COUNT_FORMS.finishes,
                    )} из ${formatInt(allRows.length)}. Места в таблице — общие по рейтингу.`
                  : `По запросу «${query.trim()}» в этом топе ничего нет.`}
              </p>
            )}

            {/* Сорвавшийся пересчёт — ровно там, куда человек смотрит: между
                фильтрами и таблицей. Раньше сообщение уезжало в самый верх
                страницы, и смена фильтра выглядела так, будто ничего не
                произошло, — таблица-то оставалась прежней. */}
            {error && (
              <div className="lb-error">
                <p>
                  {error}. В таблице ниже — прежний срез, не тот, что выбран
                  фильтрами.
                </p>
                <button type="button" className="btn btn-sm" onClick={() => void load()}>
                  Повторить
                </button>
              </div>
            )}

            {me?.row && (
              <section
                className={me.included ? "lb-me" : "lb-me lb-me-out"}
                aria-label="Ваш результат в рейтинге"
              >
                <div className="lb-me-row">
                  <span className="lb-me-rank">{formatInt(me.rank ?? 0)}</span>
                  <span className="lb-me-name">{me.row.display_name ?? "Вы"}</span>
                  <span className="lb-me-values">
                    <span className="lb-me-value">
                      <span className="lb-me-platform">Ваш лучший результат</span>
                      <span className="lb-best-time">
                        {formatFinishTime(me.row.finish_time_sec)}
                      </span>
                    </span>
                    <span className="lb-me-value lb-me-value-wide">
                      <span className="lb-me-platform">Где и когда</span>
                      <span className="lb-last-win">
                        <LocationCell row={me.row} /> <DateCell row={me.row} />
                      </span>
                    </span>
                  </span>
                  <div className="lb-me-actions">
                    {myIndex >= 0 && (
                      <button type="button" className="btn btn-ghost btn-sm" onClick={showMyRow}>
                        Показать в таблице
                      </button>
                    )}
                    {!me.included && (
                      <p className="lb-me-percentile muted">
                        В таблице первые {formatInt(data.limit)}{" "}
                        {data.mode === "runners" ? "участников — вы" : "результатов — ваш"}{" "}
                        пока ниже.
                      </p>
                    )}
                  </div>
                </div>
              </section>
            )}

            <TableWrap
              innerRef={attachFloatingHead}
              className={`lb-table-wrap${tableColumns.hasToggle ? "" : " lb-table-wrap-flat"}`}
              outerRef={tableColumns.measureRef}
            >
              <table
                ref={tableRef}
                className="data-table lb-table lb-fastest-table"
                style={{ minWidth: tableColumns.minWidth }}
              >
                <thead>
                  <tr>
                    <th className="lb-col-rank">Место</th>
                    <th className="lb-col-name">Участник</th>
                    <th className="lb-col-time">Время</th>
                    {show("location") && <th className="lb-col-home">Локация</th>}
                    {show("date") && <th className="lb-col-date">Дата</th>}
                    {show("platform") && <th className="lb-col-system">Система</th>}
                    {/* Подсказки у «Группы» здесь нет намеренно: она стоит у
                        одноимённого фильтра выше, а в шапке из-за значка
                        заголовок переставал влезать в одну строку. */}
                    {show("age") && <th className="lb-col-age">Группа</th>}
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.map((row, index) => {
                    const isMe = myRowKey != null && row.row_key === myRowKey;
                    return (
                      <tr
                        key={`${row.rank}-${row.row_key}-${row.event_date}-${index}`}
                        ref={isMe && index === myIndex ? myRowRef : undefined}
                        className={isMe ? "lb-row-me" : undefined}
                      >
                        <td className="lb-col-rank">{formatInt(row.rank)}</td>
                        <td className="lb-col-name">
                          <ParticipantName row={row} />
                        </td>
                        <td className="lb-col-time">
                          <span className="lb-best-time">
                            {formatFinishTime(row.finish_time_sec, row.finish_time_display)}
                          </span>
                        </td>
                        {show("location") && (
                          <td className="lb-col-home">
                            <LocationCell row={row} />
                          </td>
                        )}
                        {show("date") && (
                          <td className="lb-col-date">
                            <DateCell row={row} />
                          </td>
                        )}
                        {show("platform") && (
                          <td className="lb-col-system">
                            <PlatformBadge code={row.platform} />
                          </td>
                        )}
                        {show("age") && (
                          <td className="lb-col-age">
                            {row.age_group ? (
                              <span className="lb-fastest-age">{row.age_group}</span>
                            ) : (
                              <span className="lb-zero">—</span>
                            )}
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableWrap>

            {rows.length > visibleCount && (
              <div className="lb-more">
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => setVisibleCount((count) => count + PAGE_STEP)}
                >
                  Показать ещё (места {visibleCount + 1}–{nextChunkEnd})
                </button>
              </div>
            )}

            {/* Липкая строка «Вы» нужна, только когда она в таблице есть: иначе
                её кнопка «Показать» вела бы в никуда. */}
            {me?.row && me.rank != null && myIndex >= 0 && !searching && (
              <PinnedMeBar
                rowRef={myRowRef}
                tableRef={tableRef}
                rank={me.rank}
                name={me.row.display_name?.trim() || "Вы"}
                value={formatFinishTime(me.row.finish_time_sec) ?? "—"}
                onShow={showMyRow}
                watchKey={`${filters.mode}-${visibleCount}`}
              />
            )}
          </div>
        )}
      </div>
    </PortalSectionShell>
  );
}
