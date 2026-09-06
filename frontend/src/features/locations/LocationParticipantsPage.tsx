import { useEffect, useMemo, useState } from "react";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { ScrollToTopButton } from "../../components/ScrollToTopButton";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import {
  FilterGroup,
  FilterPanel,
  FilterRow,
  FilterSearch,
  FilterSelect,
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
import { formatDate, formatInt, platformCodeLabel, pluralizeRu } from "../../lib/format";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { TableViewToggle } from "../../components/tableUx/TableViewToggle";
import { useTableColumns } from "../../components/tableUx/useTableColumns";
import type { AdaptiveColumn } from "../../components/tableUx/useAdaptiveColumns";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { locationHintFor, rememberLocationHint } from "../../lib/locationHint";
import { VolunteerRolesModal } from "../leaderboards/VolunteerRolesModal";
// Стили шторки ролей (.vrm-*) и самой «шестерёнки» (.lb-roles-gear) живут в
// рейтингах вместе с компонентом. Тянем их сюда, а не копируем в index.css:
// фильтр обязан выглядеть одинаково в обеих витринах.
import "../leaderboards/leaderboards.css";
import {
  getVolunteerRoleCatalog,
  presetRoleKeys,
  type VolunteerRoleItem,
  type VolunteerRolePreset,
} from "../leaderboards/leaderboardsApi";

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

/** Сколько строк показываем сразу и сколько добавляет «Показать ещё». */
const PAGE_STEP = 100;

/**
 * Ступени фильтра «участий здесь».
 *
 * Ниже тройки страница не опускается: это порог самого списка на бэкенде
 * (LOCATION_ACTIVE_MIN_COUNT), и с двойкой он превратился бы в перепись всех,
 * кто когда-либо здесь пробегал. Верх ступеней подрезается рекордом площадки,
 * чтобы в перечне не висели заведомо пустые значения.
 */
const MIN_COUNT_STEPS = [3, 5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 150, 200, 300, 500];

// Подпись на «шестерёнке» ролей и хвост в заголовке — те же слова, что в
// рейтингах волонтёрского туризма: фильтр один и тот же, и называться он должен
// одинаково, в какой бы витрине ни встретился.
const ROLE_PRESET_SHORT: Record<VolunteerRolePreset, string> = {
  all: "все роли",
  on_site: "на площадке",
  on_site_no_run: "вместо бега",
  remote: "не приезжая",
  custom: "свой набор",
};

const ROLE_PRESET_LEAD: Record<VolunteerRolePreset, string> = {
  all: "",
  on_site: "Только роли, ради которых надо приехать на старт.",
  on_site_no_run: "Только роли, которые нельзя совместить с пробежкой.",
  remote: "Только роли, которые выполняют не приезжая на площадку.",
  custom: "Только выбранные роли.",
};

const ROLE_FILTER_HINT =
  "Какие волонтёрские роли идут в зачёт: только те, ради которых надо приехать " +
  "на старт, только «вместо бега» или свой набор. По умолчанию — все роли.";

// Та же шторка, что в рейтингах, но здесь она собирает состав, а не место.
const ROLES_MODAL_INTRO =
  "Вы сами выбираете, из каких ролей считать состав: возьмите готовый набор или " +
  "отметьте роли вручную.";

// Честно предупреждаем про ожидание: срез считается на сервере, и на большой
// площадке первый пересчёт заметен. Лучше сказать заранее, чем оставить
// человека гадать, завис фильтр или считает (просьба Дмитрия 06.09.2026).
const ROLES_MODAL_NOTE =
  "Таблица под выбранные роли пересобирается на сервере: у большой площадки первый " +
  "такой срез может считаться до минуты. Тот же набор ролей дальше открывается сразу.";

/**
 * Зачёт ролей из адреса: `?roles=on_site` (пресет) или `?roles=k1,k2` (свой
 * набор). Ссылку на «постоянных маршалов» площадки так можно кинуть в чат
 * организаторов — она откроется с тем же срезом.
 */
function rolePresetFromLocation(): { preset: VolunteerRolePreset; keys: string[] } {
  if (typeof window === "undefined") {
    return { preset: "all", keys: [] };
  }
  const raw = new URLSearchParams(window.location.search).get("roles");
  if (!raw) {
    return { preset: "all", keys: [] };
  }
  if (raw === "on_site" || raw === "on_site_no_run" || raw === "remote") {
    return { preset: raw, keys: [] };
  }
  const keys = raw.split(",").map((key) => key.trim()).filter(Boolean);
  return keys.length > 0 ? { preset: "custom", keys } : { preset: "all", keys: [] };
}

/** Участий в выбранной системе — или во всех сразу, когда система не выбрана. */
function countIn(row: LocationActiveParticipant, platform: string): number {
  if (platform === "all") {
    return row.count;
  }
  return row.platform_counts?.[platform] ?? 0;
}

function LocationParticipantsContent({ slug }: { slug: string }) {
  const [initialScope] = useState(scopeFromLocation);
  const [data, setData] = useState<LocationParticipants | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState<Scope>(initialScope);
  const [sort, setSort] = useState<SortState>({ key: "place", asc: true });
  const [query, setQuery] = useState("");
  // Система, по которой смотрим состав («all» — все сразу).
  const [platform, setPlatform] = useState("all");
  // Порог участий: сколько раз человек должен был здесь отметиться, чтобы
  // попасть в список. Стартуем с нижней ступени — это порог самого списка.
  const [minCount, setMinCount] = useState(MIN_COUNT_STEPS[0]);
  // Срез волонтёрского зачёта по ролям. Пресет — что выбрано в шторке, keys —
  // конкретные ключи ролей (у пресета они подставляются из справочника).
  const [initialRoles] = useState(rolePresetFromLocation);
  const [rolePreset, setRolePreset] = useState<VolunteerRolePreset>(initialRoles.preset);
  const [roleKeys, setRoleKeys] = useState<string[]>(initialRoles.keys);
  const [roleCatalog, setRoleCatalog] = useState<VolunteerRoleItem[]>([]);
  const [rolesModalOpen, setRolesModalOpen] = useState(false);
  // Волонтёрские строки в срезе по ролям приезжают отдельным запросом: бегунов
  // роли не касаются, и гонять их заново незачем.
  const [roleRows, setRoleRows] = useState<{
    key: string;
    rows: LocationActiveParticipant[];
    peopleTotal: number;
  } | null>(null);
  const [rolesLoading, setRolesLoading] = useState(false);
  // Показываем по сотне строк, как в рейтингах: у крупной площадки список
  // уходит за тысячу, и рисовать его целиком незачем.
  const [visibleCount, setVisibleCount] = useState(PAGE_STEP);
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

  // Ключи, которые реально уходят в запрос: у «всех ролей» фильтра нет вовсе.
  const effectiveRoles = rolePreset === "all" ? null : roleKeys;
  const roleFilterKey = effectiveRoles ? [...effectiveRoles].sort().join(",") : "";

  // Справочник ролей нужен и шторке, и пресетам (в них ключи подставляются из
  // него). Тянем один раз, когда человек впервые открыл волонтёрский зачёт или
  // пришёл по ссылке с готовым срезом.
  useEffect(() => {
    if (roleCatalog.length > 0 || (scope !== "volunteers" && rolePreset === "all")) {
      return;
    }
    let cancelled = false;
    getVolunteerRoleCatalog()
      .then((catalog) => {
        if (!cancelled) {
          setRoleCatalog(catalog.roles);
        }
      })
      .catch(() => {
        // Справочник не приехал — страница живёт без фильтра ролей, это не
        // повод показывать ошибку вместо состава.
      });
    return () => {
      cancelled = true;
    };
  }, [scope, rolePreset, roleCatalog.length]);

  // Пресет знает только своё имя; ключи к нему приходят из справочника — в том
  // числе когда срез приехал ссылкой (?roles=on_site) и выбирать его руками
  // никто не будет.
  useEffect(() => {
    if (roleCatalog.length === 0 || rolePreset === "all" || rolePreset === "custom") {
      return;
    }
    if (roleKeys.length === 0) {
      setRoleKeys(presetRoleKeys(rolePreset, roleCatalog) ?? []);
    }
  }, [roleCatalog, rolePreset, roleKeys.length]);

  // Волонтёрские строки под выбранные роли.
  useEffect(() => {
    if (!roleFilterKey) {
      setRoleRows(null);
      setRolesLoading(false);
      return;
    }
    let cancelled = false;
    setRolesLoading(true);
    getLocationParticipants(slug, roleFilterKey.split(","))
      .then((payload) => {
        if (!cancelled) {
          setRoleRows({
            key: roleFilterKey,
            rows: payload.volunteers,
            peopleTotal: payload.volunteers_people_total,
          });
        }
      })
      .catch(() => {
        // Срез не посчитался — остаёмся на полном зачёте, а не на пустоте.
        if (!cancelled) {
          setRoleRows(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setRolesLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug, roleFilterKey]);

  const applyRoleFilter = (preset: VolunteerRolePreset, keys: string[]) => {
    setRolePreset(preset);
    setRoleKeys(keys);
    setRolesModalOpen(false);
    // Срез — в адресе: ссылку с ним можно отправить, а F5 её не потеряет.
    // localStorage сознательно не трогаем, в отличие от рейтингов: там фильтр
    // общий на витрину, здесь он про конкретную площадку, и «прилипший» срез
    // на соседней локации только запутает.
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      if (preset === "all") {
        url.searchParams.delete("roles");
      } else {
        url.searchParams.set("roles", preset === "custom" ? keys.join(",") : preset);
      }
      window.history.replaceState(null, "", url.toString());
    }
  };

  // Срез по системе — отдельным слоем: по нему считаются доступные ступени
  // порога, а сортировка и сам порог накладываются уже поверх.
  const platformRows = useMemo(() => {
    if (!data) {
      return [];
    }
    // Волонтёров в срезе по ролям считает бэкенд: сложить их из строк «все
    // роли» нельзя — человек, вышедший в одну субботу в двух ролях, дал бы
    // два волонтёрства вместо одного. Пока новый срез едет, показываем
    // предыдущий, а не пустоту.
    const source =
      scope === "runners"
        ? data.runners
        : roleFilterKey && roleRows
          ? roleRows.rows
          : data.volunteers;
    if (platform === "all") {
      return source;
    }
    // Берём участия только в выбранной системе, отсекаем тех, кто в ней не
    // дотянул до порога списка, и заново нумеруем места — иначе в списке
    // остались бы дыры от выпавших строк.
    const scoped = source
      .map((row) => ({
        ...row,
        count: countIn(row, platform),
        // Стаж тоже по выбранной системе: у площадки, сменившей систему,
        // общий «первый старт» приходится на прежнюю и к срезу отношения
        // не имеет (Швейцария, 03.09.2026). Если разбивки в ответе нет
        // вовсе — это старый payload из кэша, показываем общую дату, а не
        // пустоту.
        first_date: row.platform_first_dates
          ? (row.platform_first_dates[platform] ?? null)
          : row.first_date,
        last_date: row.platform_last_dates
          ? (row.platform_last_dates[platform] ?? null)
          : row.last_date,
      }))
      .filter((row) => row.count >= data.min_count)
      .sort((a, b) => b.count - a.count || (a.name ?? "").localeCompare(b.name ?? ""));
    // Место по-спортивному: равное число участий — равное место, следующий
    // за парой третьих получает пятое. Считаем бегущим значением: в map()
    // третий аргумент — ИСХОДНЫЙ массив, и место соседа там ещё старое.
    let place = 0;
    let previous: number | null = null;
    return scoped.map((row, index) => {
      if (row.count !== previous) {
        place = index + 1;
        previous = row.count;
      }
      return { ...row, place };
    });
  }, [data, scope, platform, roleFilterKey, roleRows]);

  // Нижняя ступень — порог самого списка с бэкенда: строк ниже него в ответе
  // просто нет. Верхняя — рекорд площадки в текущем срезе: ступени выше
  // очищали бы таблицу до пустоты.
  const minCountFloor = data?.min_count ?? MIN_COUNT_STEPS[0];
  const minCountOptions = useMemo(() => {
    const top = platformRows.reduce((best, row) => Math.max(best, row.count), 0);
    const steps = MIN_COUNT_STEPS.filter((step) => step >= minCountFloor && step <= top);
    return steps.length > 0 ? steps : [minCountFloor];
  }, [platformRows, minCountFloor]);

  // Смена зачёта или системы опускает потолок: у волонтёров рекорд обычно
  // куда скромнее, чем у бегунов. Съезжаем на самую высокую доступную
  // ступень, иначе в фильтре остаётся значение, которого в перечне уже нет.
  useEffect(() => {
    if (!minCountOptions.includes(minCount)) {
      setMinCount(minCountOptions[minCountOptions.length - 1]);
    }
  }, [minCountOptions, minCount]);

  const scopeRows = useMemo(() => {
    // Строки идут по убыванию участий, поэтому порог срезает ровно хвост —
    // места оставшихся остаются теми же, что и в полном списке.
    const scoped = platformRows.filter((row) => row.count >= minCount);
    const sorted = [...scoped];
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
  }, [platformRows, minCount, sort]);

  const rows = useMemo(() => {
    const needle = normalizeName(query);
    if (!needle) {
      return scopeRows;
    }
    return scopeRows.filter((row) => normalizeName(row.name ?? "").includes(needle));
  }, [scopeRows, query]);

  // Смена зачёта, системы, порога или запроса начинает список заново.
  useEffect(() => {
    setVisibleCount(PAGE_STEP);
  }, [scope, platform, minCount, query]);

  const shownRows = useMemo(() => rows.slice(0, visibleCount), [rows, visibleCount]);
  const hasMore = rows.length > shownRows.length;

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

  const visibleColumnCount = PEOPLE_COLUMNS.filter((column) => show(column.key)).length;

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
  // Фильтр по системе нужен только там, где площадка жила больше чем в одной.
  const platformOptions = data.platform_codes ?? [];
  const hasParkrun = platformOptions.includes("parkrun");
  const peopleTotal =
    scope === "runners"
      ? data.runners_people_total
      : roleFilterKey && roleRows
        ? roleRows.peopleTotal
        : data.volunteers_people_total;
  // «от 3 раз» вместо «от 3 пробежек»: слово одно на оба зачёта, а падежи
  // числительного с двумя наборами слов читаются хуже, чем нейтральное «раз».
  const threshold = `от ${minCount} раз`;

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
            {scope === "volunteers" && rolePreset !== "all" && (
              <> {ROLE_PRESET_LEAD[rolePreset]}</>
            )}
            {hasParkrun && scope === "volunteers" && (
              <>
                {" "}
                Годы parkrun сюда не входят: поимённых списков волонтёров той эпохи не
                сохранилось — в отличие от протоколов финишёров, которые собраны целиком.
              </>
            )}
          </span>
        </div>
      </header>

      {/* Зачёт и поиск — одной строкой: два ряда управления над таблицей
          съедали экран телефона до первой фамилии. */}
      <FilterPanel>
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
                  (key === "runners"
                    ? data.runners
                    : roleFilterKey && roleRows
                      ? roleRows.rows
                      : data.volunteers
                  ).length,
                )})`,
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
          {/* Роли — только в волонтёрском зачёте: у пробежек ролей нет.
              Шторка и слова те же, что в рейтингах волонтёрского туризма
              (просьба Дмитрия 06.09.2026: «такой же фильтр»), поэтому и
              компонент переиспользуем, а не копируем. */}
          {scope === "volunteers" && roleCatalog.length > 0 && (
            <FilterGroup
              label="Роли"
              hint={
                <StatHintTooltip text={ROLE_FILTER_HINT}>
                  <span className="loc-section-title-info" aria-label="Про фильтр ролей">
                    ⓘ
                  </span>
                </StatHintTooltip>
              }
            >
              <button
                type="button"
                className={`lb-roles-gear${rolePreset !== "all" ? " lb-roles-gear-active" : ""}`}
                aria-label="Настроить, какие роли считать"
                title="Какие роли считать"
                onClick={() => setRolesModalOpen(true)}
              >
                <span aria-hidden="true">⚙</span>
                <span className="lb-roles-gear-text">{ROLE_PRESET_SHORT[rolePreset]}</span>
              </button>
            </FilterGroup>
          )}
          {/* Порог участий: с тройкой в списке крупной площадки тонут
              локалы — на «от 3» половина строк это туристы, забежавшие сюда
              трижды за годы (Дмитрий 06.09.2026). Показываем фильтр только
              там, где ступень выше нижней вообще кому-то по силам. */}
          {minCountOptions.length > 1 && (
            <FilterGroup label="Участий здесь">
              <FilterSelect
                ariaLabel="Сколько раз человек был здесь"
                value={minCount}
                onChange={setMinCount}
                options={minCountOptions.map((step) => ({
                  value: step,
                  label: `от ${step}`,
                }))}
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
        {rolesLoading && (
          <span className="muted loc-people-found">
            Считаем срез по ролям — на большой площадке это может занять до минуты…
          </span>
        )}
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
              {shownRows.length === 0 ? (
                <tr>
                  <td colSpan={visibleColumnCount} className="table-empty-cell">
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
                shownRows.map((row, index) => (
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
          {/* Знаменатель считается по всем системам сразу, поэтому в срезе
              одной системы его не показываем — «N из M» было бы про разное. */}
          {scopeRows.length > 0 && peopleTotal > 0 && platform === "all" ? (
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
      {rolesModalOpen && roleCatalog.length > 0 && (
        <VolunteerRolesModal
          roles={roleCatalog}
          preset={rolePreset}
          selected={rolePreset === "all" ? roleCatalog.map((role) => role.key) : roleKeys}
          intro={ROLES_MODAL_INTRO}
          note={ROLES_MODAL_NOTE}
          onApply={applyRoleFilter}
          onClose={() => setRolesModalOpen(false)}
        />
      )}
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
