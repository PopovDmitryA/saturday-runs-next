import { useEffect, useMemo, useState } from "react";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { ScrollToTopButton } from "../../components/ScrollToTopButton";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import {
  FilterGroup,
  FilterPanel,
  FilterRow,
  FilterSearch,
  FilterTabs,
} from "../../components/filters/FilterPanel";
import {
  ApiError,
  getLocationParticipants,
  type LocationActiveParticipant,
  type LocationParticipants,
} from "../../lib/api";
import { applyPageMeta, locationPageMeta } from "../../lib/pageMeta";
import { flushMetrikaHit } from "../../lib/metrika";
import { formatDate, formatInt, pluralizeRu } from "../../lib/format";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { TableViewToggle } from "../../components/tableUx/TableViewToggle";
import { useTableColumns } from "../../components/tableUx/useTableColumns";
import type { AdaptiveColumn } from "../../components/tableUx/useAdaptiveColumns";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { locationHintFor, rememberLocationHint } from "../../lib/locationHint";

type Scope = "runners" | "volunteers";
type SortKey = "place" | "name" | "count" | "total" | "share" | "first" | "last";
type SortState = { key: SortKey; asc: boolean };

/** Доля участий на этой площадке от всех участий человека, 0…1. */
function shareHere(row: LocationActiveParticipant): number | null {
  if (!row.total_count) {
    return null;
  }
  return row.count / row.total_count;
}

function sortValue(row: LocationActiveParticipant, key: SortKey): number | string | null {
  switch (key) {
    case "place":
      return row.place;
    case "name":
      return row.name ?? "";
    case "count":
      return row.count;
    case "total":
      return row.total_count;
    case "share":
      return shareHere(row);
    case "first":
      return row.first_date;
    case "last":
      return row.last_date;
  }
}

// Колонки в порядке важности: сначала то, ради чего страницу открывают
// (кто, сколько раз, когда был в последний раз), потом контекст — сколько у
// человека участий вообще и с какого старта он здесь свой.
// Ширины — под самый узкий телефон: обязательная тройка (место, имя, число)
// должна умещаться без горизонтального скролла, а на широком экране колонки
// разъезжаются сами (table-layout: fixed делит остаток пропорционально).
const PEOPLE_COLUMNS: AdaptiveColumn[] = [
  { key: "place", width: 56, required: true },
  { key: "name", width: 150, required: true },
  { key: "count", width: 130, required: true },
  { key: "last", width: 150 },
  { key: "total", width: 120 },
  { key: "share", width: 130 },
  { key: "first", width: 150 },
];

const SCOPE_WORDS: Record<Scope, { label: string; column: string; verb: string }> = {
  runners: { label: "Бегуны", column: "Пробежек", verb: "бегал" },
  volunteers: { label: "Волонтёры", column: "Волонтёрств", verb: "волонтёрил" },
};

/**
 * Зачёт из адреса: со страницы локации ссылка блока волонтёров ведёт сразу
 * на волонтёрский список, иначе человек попадал на бегунов и переключался сам.
 */
function scopeFromLocation(): Scope {
  if (typeof window === "undefined") {
    return "runners";
  }
  return new URLSearchParams(window.location.search).get("scope") === "volunteers"
    ? "volunteers"
    : "runners";
}

function LocationParticipantsContent({ slug }: { slug: string }) {
  const [initialScope] = useState(scopeFromLocation);
  const [data, setData] = useState<LocationParticipants | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState<Scope>(initialScope);
  const [sort, setSort] = useState<SortState>({ key: "place", asc: true });
  const [query, setQuery] = useState("");
  // Копия шапки встаёт под липкую полосу «Кратко | Полно», а не под шапку сайта.
  const attachFloatingHead = useFloatingTableHead(".tview-bar");
  const tableColumns = useTableColumns(PEOPLE_COLUMNS);
  const showFull = tableColumns.showFull;
  const show = tableColumns.show;

  useEffect(() => {
    let cancelled = false;
    getLocationParticipants(slug)
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
          rememberLocationHint({ slug: payload.slug, name: payload.name });
          applyPageMeta(locationPageMeta(payload, { participants: true }));
          flushMetrikaHit();
        }
      })
      .catch((err) => {
        if (cancelled) {
          return;
        }
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError(err instanceof Error ? err.message : "Не удалось загрузить состав локации");
        }
        // Просмотр был, пусть и неудачный — досылаем с родовым заголовком.
        flushMetrikaHit();
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  const scopeRows = useMemo(() => {
    if (!data) {
      return [];
    }
    const source = scope === "runners" ? data.runners : data.volunteers;
    const sorted = [...source];
    sorted.sort((a, b) => {
      const left = sortValue(a, sort.key);
      const right = sortValue(b, sort.key);
      if (left === right) {
        // Равные значения — по месту в рейтинге, чтобы порядок не «дрожал».
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
  }, [data, scope, sort]);

  const rows = useMemo(() => {
    const needle = normalizeName(query);
    if (!needle) {
      return scopeRows;
    }
    return scopeRows.filter((row) => normalizeName(row.name ?? "").includes(needle));
  }, [scopeRows, query]);

  const toggleSort = (key: SortKey) => {
    setSort((current) =>
      current.key === key
        ? { key, asc: !current.asc }
        : // Числа интереснее сверху вниз, имена и место — наоборот.
          { key, asc: key === "place" || key === "name" },
    );
  };

  const sortProps = (key: SortKey) => ({
    filterable: false,
    sortActive: sort.key === key,
    sortAsc: sort.asc,
    onSort: () => toggleSort(key),
  });

  const visibleCount = PEOPLE_COLUMNS.filter((column) => show(column.key)).length;

  // Пока данные едут, имя берём из подсказки — иначе подпункт сайдбара с
  // названием площадки мигает при каждом переходе внутри локации.
  const sidebarLocation = data ? { slug: data.slug, name: data.name } : locationHintFor(slug);

  if (notFound) {
    return (
      <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
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
      <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
        <div className="card error">
          <p>{error}</p>
        </div>
      </PortalSectionShell>
    );
  }

  if (!data) {
    return (
      <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
        <p className="muted">Загрузка…</p>
      </PortalSectionShell>
    );
  }

  const words = SCOPE_WORDS[scope];
  const peopleTotal = scope === "runners" ? data.runners_people_total : data.volunteers_people_total;
  // «от 3 раз» вместо «от 3 пробежек»: порог один на оба зачёта, а падежи
  // числительного с двумя наборами слов читаются хуже, чем нейтральное «раз».
  const threshold = `от ${data.min_count} раз`;

  return (
    <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
      <header className="loc-header loc-wide-page">
        <p className="muted loc-header-breadcrumb">
          <a href="/locations">← Все локации</a> /{" "}
          <a href={`/locations/${data.slug}`}>{data.name}</a> / Постоянный состав
        </p>
        {/* Условие отбора стоит строкой в заголовке, а не абзацем под ним:
            это полстроки текста, ради которых не стоит отодвигать таблицу. */}
        <div className="loc-header-title">
          <h1>{data.name} — постоянный состав</h1>
          <span className="muted loc-people-lead">
            Все, кто {words.verb} здесь {threshold}.
          </span>
        </div>
      </header>

      {/* Зачёт и поиск — одной строкой: два ряда управления над таблицей
          съедали экран телефона до первой фамилии. */}
      <FilterPanel className="loc-people-toolbar">
        <FilterRow>
          <FilterGroup label="Зачёт">
            <FilterTabs
              asTablist
              ariaLabel="Зачёт"
              value={scope}
              onChange={(value) => setScope(value as Scope)}
              options={(["runners", "volunteers"] as Scope[]).map((key) => ({
                value: key,
                label: `${SCOPE_WORDS[key].label} (${formatInt(
                  (key === "runners" ? data.runners : data.volunteers).length,
                )})`,
              }))}
            />
          </FilterGroup>
          <FilterGroup trailing>
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
        <TableViewToggle columns={tableColumns} />
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
            style={showFull ? undefined : { minWidth: tableColumns.minWidth }}
          >
            <colgroup>
              <col className="col-place" />
              <col className="col-name" />
              <col className="col-count" />
              {show("last") && <col className="col-date" />}
              {show("total") && <col className="col-total" />}
              {show("share") && <col className="col-share" />}
              {show("first") && <col className="col-date" />}
            </colgroup>
            <thead>
              <tr>
                <ColumnHeader
                  label="#"
                  headerTitle={`Место в зачёте локации: равное число участий — равное место`}
                  {...sortProps("place")}
                />
                <ColumnHeader label="Участник" {...sortProps("name")} />
                <ColumnHeader
                  label={words.column}
                  hint="Участий на этой локации"
                  {...sortProps("count")}
                />
                {show("last") && (
                  <ColumnHeader
                    label="Последний старт"
                    hint="Когда человек был здесь в последний раз"
                    {...sortProps("last")}
                  />
                )}
                {show("total") && (
                  <ColumnHeader
                    label="Всего"
                    hint="Участий во всех локациях вместе, а не только здесь"
                    {...sortProps("total")}
                  />
                )}
                {show("share") && (
                  <ColumnHeader
                    label="Доля здесь"
                    hint="Какую часть всех своих участий человек провёл на этой площадке"
                    {...sortProps("share")}
                  />
                )}
                {show("first") && (
                  <ColumnHeader
                    label="Первый старт"
                    hint="Когда человек появился здесь впервые"
                    {...sortProps("first")}
                  />
                )}
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={visibleCount} className="table-empty-cell">
                    <span className="muted">
                      {query.trim()
                        ? "Никого не нашли — попробуйте другую часть имени"
                        : `Здесь пока никто не ${words.verb} ${threshold}`}
                    </span>
                  </td>
                </tr>
              ) : (
                // Ключ по индексу намеренно: строки не несут собственного
                // состояния, а имя с местом уникальными не бывают — у человека
                // с непривязанными аккаунтами в разных системах строки
                // полностью совпадают (React ругался на дубли ключей и
                // перемешивал порядок при смене зачёта).
                rows.map((row, index) => (
                  <tr key={`${scope}-${index}`}>
                    <td className="td-compact loc-people-place">{row.place}</td>
                    <td>
                      <PersonName name={row.name} handle={row.handle} />
                    </td>
                    <td className="td-compact">{formatInt(row.count)}</td>
                    {show("last") && <td className="td-date">{formatDay(row.last_date)}</td>}
                    {show("total") && <td className="td-compact">{formatInt(row.total_count)}</td>}
                    {show("share") && <td className="td-compact">{formatShare(row)}</td>}
                    {show("first") && <td className="td-date">{formatDay(row.first_date)}</td>}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </TableWrap>
        <p className="table-foot muted">
          {scopeRows.length > 0 && peopleTotal > 0 ? (
            <>
              {formatInt(scopeRows.length)} из{" "}
              {pluralizeRu(peopleTotal, ["человека", "человек", "человек"])},
              кто {words.verb} здесь хотя бы раз.{" "}
            </>
          ) : null}
          <StatHintTooltip text="Если у человека единый профиль на сайте (привязаны аккаунты нескольких систем), участия во всех системах суммируются в одну строку. Без привязки аккаунты разных систем объединить нельзя — они остаются отдельными строками.">
            <span className="loc-section-title-info" aria-label="Как считается">
              ⓘ
            </span>
          </StatHintTooltip>{" "}
          Как считается
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

/** Регистр и «ё» поиску не мешают: «фёдоров» находит «ФЕДОРОВ» и наоборот. */
function normalizeName(value: string): string {
  return value.trim().toLowerCase().replace(/ё/g, "е");
}

/** Дата или прочерк: у волонтёрских строк дореформенной эпохи даты может не быть. */
function formatDay(value: string | null): string {
  return value ? formatDate(value) : "—";
}

function formatShare(row: LocationActiveParticipant): string {
  const share = shareHere(row);
  if (share === null) {
    return "—";
  }
  // До целых: доли процента здесь ничего не решают, а «32,4 %» шумит.
  return `${Math.round(share * 100)}%`;
}

// Состав локации открыт без логина, как и вся витрина локаций.
export function LocationParticipantsPage({ slug }: { slug: string }) {
  return <LocationParticipantsContent slug={slug} />;
}
