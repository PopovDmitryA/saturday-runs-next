import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import {
  COUNT_BY_LABELS,
  GENDERED_METRICS,
  getLeaderboard,
  getMyLeaderboardRow,
  getVolunteerRoleCatalog,
  presetRoleKeys,
  ROLE_FILTER_METRICS,
  type VolunteerRoleItem,
  type VolunteerRolePreset,
  MIN_VISITS_METRICS,
  MIN_VISITS_OPTIONS,
  PLATFORM_LABELS,
  type CountBy,
  type LeaderboardGender,
  type LeaderboardMetric,
  type LeaderboardResponse,
  type LeaderboardRow,
  type MyLeaderboardRow,
  type PlatformFilter,
  type VolunteerRoleDetail,
  type WeekLocation,
} from "./leaderboardsApi";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { unitLabel } from "./pluralize";
import { RatingsLoginBanner } from "./RatingsLoginBanner";
import { VolunteerRolesModal } from "./VolunteerRolesModal";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { TableViewToggle, useTableView } from "../../components/tableUx/TableViewToggle";
import { useNarrowViewport } from "../../components/tableUx/useNarrowViewport";
import { PinnedMeBar } from "../../components/tableUx/PinnedMeBar";
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

// Колонки-системы больше не сортируются: чтобы посмотреть зачёт одной системы,
// есть фильтр «Система» — он пересчитывает и место, и «Всего», а не просто
// переставляет строки по одному столбцу (решение Дмитрия 01.08.2026).
type SortKey = "rank" | "total" | "best_time";

const METRIC_CRUMBS: Record<LeaderboardMetric, { section: string; label: string }> = {
  runs: { section: "Бегуны", label: "Количество пробежек" },
  volunteering: { section: "Волонтёры", label: "Количество волонтёрств" },
  volunteer_roles: { section: "Волонтёры", label: "Мультиволонтёр" },
  locations: { section: "Паркран-туристы", label: "Уникальные локации" },
  volunteer_locations: { section: "Волонтёры", label: "Уникальные локации" },
  wins: { section: "Бегуны", label: "Количество первых мест" },
  win_locations: { section: "Паркран-туристы", label: "Локации с первым местом" },
};

// Мультиволонтёр: колонка «Любимая роль» + детализация ролей по клику на
// строке (в отдельную колонку список не влезает — у ветеранов ролей под три
// десятка).
const TOP_ROLE_HINT =
  "Роль, в которой участник выходил чаще всего. Число рядом — сколько раз он " +
  "волонтёрил именно в ней.";

const ROLES_TOTAL_HINT =
  "Число разных освоенных ролей, а не число волонтёрств. Одна и та же роль в " +
  "разных системах считается один раз — кликните по строке, чтобы увидеть разбор.";

const TOP_LOCATION_HINT =
  "Число рядом — сколько раз участник был первым именно на этой локации, " +
  "а не сколько раз там бегал.";

// Фильтр туризма: «локация засчитывается от N визитов». Кнопка 1+ — обычный
// рейтинг (любая локация, где человек был хоть раз), дальше планка растёт.
// У волонтёрского туризма визит — это волонтёрство, а не забег.
const MIN_VISITS_HINT =
  "Сколько раз надо пробежать на локации, чтобы она дала балл. " +
  "При «3+» разовые заезды в зачёт не идут.";

const VOLUNTEER_MIN_VISITS_HINT =
  "Сколько раз надо отволонтёрить на локации, чтобы она дала балл. " +
  "При «3+» разовые визиты в зачёт не идут. Несколько ролей в одну субботу — одно волонтёрство.";

// Фильтр «смотреть по одной системе» — общий для всех рейтингов. По умолчанию
// объединённый зачёт (у туризма одна физическая площадка = одна локация, в
// какой бы системе ни бежали); кнопка системы переключает на зачёт только по
// ней, с пересчётом места и «Всего» — это не колонка, а отдельный рейтинг.
// Список кнопок приходит с бэкенда (platform_options): в волонтёрском туризме
// parkrun отсутствует.
const PLATFORM_TAB_LABELS: Record<string, string> = {
  all: "Все",
  ...PLATFORM_LABELS,
};

const ROLE_PRESET_SHORT: Record<string, string> = {
  all: "все роли",
  on_site: "на площадке",
  on_site_no_run: "вместо бега",
  remote: "не приезжая",
  custom: "свой набор",
};

const ROLE_FILTER_HINT =
  "Какие волонтёрские роли идут в зачёт: только те, ради которых надо приехать " +
  "на старт, только «вместо бега» или свой набор. По умолчанию — все роли.";

const PLATFORM_FILTER_HINT =
  "Переключает объединённый зачёт на одну систему: место и «Всего» " +
  "пересчитываются заново, столбцы остальных систем скрываются.";

// Гео-зачёт туристических рейтингов: колонки «Городов»/«Регионов» видны всегда,
// а фильтр «Считаем» переключает, что идёт в «Всего» и по чему строится место —
// 30 площадок в одной Москве и 10 площадок в 10 регионах это разные достижения.
const COUNT_BY_HINT =
  "Что считает «Всего» и по чему строится место. Города и регионы видны " +
  "столбцами при любом выборе. Зарубежные старты идут по стране: одна страна — " +
  "один регион.";

const CITIES_HINT =
  "Сколько РАЗНЫХ городов набрано засчитанными площадками: несколько парков " +
  "одного города дают один город.";

const REGIONS_HINT =
  "Сколько РАЗНЫХ регионов набрано засчитанными площадками. Зарубежные старты " +
  "считаются по стране: одна страна — один регион.";

// Колонка «Последняя неделя»: площадка и дата — тем же видом, что «Последняя
// победа» в победных рейтингах. Показывает, где человек был за окно дельты,
// независимо от того, дало это +1 или нет: в туризме повторный заезд на давно
// освоенную площадку в «Всего» не идёт, но в колонке виден (решение Дмитрия
// 02.08.2026). Не был нигде — прочерк.
const WEEK_LOCATIONS_HINT: Record<string, string> = {
  runs: "Где участник бежал за последнюю неделю.",
  volunteering: "Где участник волонтёрил за последнюю неделю.",
  locations:
    "Где участник бежал за последнюю неделю. Знакомая площадка «Всего» не " +
    "увеличивает — новых локаций она не добавляет, — но в колонке видна.",
  volunteer_locations:
    "Где участник волонтёрил за последнюю неделю. Знакомая площадка «Всего» не " +
    "увеличивает — новых локаций она не добавляет, — но в колонке видна.",
};

// Сколько площадок недели показываем в ячейке — остальное сворачиваем в «+N»,
// иначе у волонтёра-многостаночника колонка растягивает всю таблицу.
const WEEK_LOCATIONS_VISIBLE = 2;

const BEST_TIME_HINT =
  "Глобальный рекорд участника: лучшее время по всем системам и локациям — " +
  "не только на пробежках, где он был первым.";

// Колонка «последней» читается по-разному у двух победных рейтингов, поэтому и
// заголовок, и подсказка разные: в рейтинге локаций считается пополнение
// коллекции (первая победа на новой площадке), в рейтинге побед — просто
// последняя победа, пусть и на давно знакомой локации.
const LAST_WIN_META: Record<string, { label: string; hint: string }> = {
  wins: {
    label: "Последняя победа",
    hint: "Локация и дата самого свежего первого места участника.",
  },
  win_locations: {
    label: "Последняя новая локация",
    hint:
      "Локация, которая последней пополнила коллекцию: дата — первая победа " +
      "именно на ней.",
  },
};

function formatBestTime(row: {
  best_time_sec?: number | null;
  best_time_display?: string | null;
}): string | null {
  const seconds = row.best_time_sec;
  if (seconds == null || seconds <= 0) {
    return row.best_time_display ?? null;
  }
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  // Компактный вид: «17:23», а не «00:17:23» — колонка узкая, часы у 5 км редкость.
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
  }
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

/**
 * Место → человеческая доля. У верхушки рейтинга «опережаете 100 %» звучит
 * абсурдно (58-й из 123 626 округлялся именно так), поэтому у топа говорим
 * «в топ-0,1 %», а ниже — сколько людей позади, с округлением вниз.
 */
function formatPercentile(rank: number, entrants: number): string {
  const topShare = (rank / entrants) * 100;
  if (topShare < 5) {
    const rounded = Math.max(0.1, Math.round(topShare * 10) / 10);
    const value = rounded.toLocaleString("ru-RU", { maximumFractionDigits: 1 });
    return `Вы в топ-${value} % участников рейтинга`;
  }
  const ahead = Math.floor(100 - topShare);
  return `Вы опережаете ${ahead} % из ${entrants.toLocaleString("ru-RU")} участников рейтинга`;
}

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

function TopRole({ row }: { row: { top_role?: string | null; top_role_count?: number | null } }) {
  if (!row.top_role) {
    return <span className="lb-zero">—</span>;
  }
  return (
    <span className="lb-home">
      {row.top_role}
      {row.top_role_count != null && row.top_role_count > 1 && (
        <span className="lb-home-count"> ×{row.top_role_count}</span>
      )}
    </span>
  );
}

// Детализация мультиволонтёра: из чего собралось «Всего» — какие роли и
// сколько волонтёрств в каждой системе. Колонки те же, что в самой таблице,
// чтобы цифры читались по вертикали.
function RoleBreakdown({
  details,
  columns,
}: {
  details: VolunteerRoleDetail[];
  columns: string[];
}) {
  if (details.length === 0) {
    return <p className="muted lb-roles-empty">Ролей пока нет.</p>;
  }
  return (
    <table className="lb-roles-table">
      <thead>
        <tr>
          <th className="lb-roles-col-name">Роль</th>
          {columns.map((code) => (
            <th key={code} className="lb-col-num">
              {PLATFORM_LABELS[code] ?? code}
            </th>
          ))}
          <th className="lb-col-num lb-col-total">Волонтёрств</th>
        </tr>
      </thead>
      <tbody>
        {details.map((detail) => (
          <tr key={detail.role}>
            <td className="lb-roles-col-name">{detail.role}</td>
            {columns.map((code) => (
              <td key={code} className="lb-col-num">
                {detail.platforms[code] ? (
                  detail.platforms[code]
                ) : (
                  <span className="lb-zero">—</span>
                )}
              </td>
            ))}
            <td className="lb-col-num lb-col-total">{detail.total}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function BestTime({
  row,
}: {
  row: { best_time_sec?: number | null; best_time_display?: string | null };
}) {
  const value = formatBestTime(row);
  if (!value) {
    return <span className="lb-zero">—</span>;
  }
  return <span className="lb-best-time">{value}</span>;
}

function LastWinLocation({
  row,
}: {
  row: {
    last_win_location?: string | null;
    last_win_location_slug?: string | null;
    last_win_date?: string | null;
  };
}) {
  if (!row.last_win_location) {
    return <span className="lb-zero">—</span>;
  }
  // Слаг может не резолвиться только у локаций без внятного external_key —
  // тогда показываем имя текстом, без битой ссылки.
  const name = row.last_win_location_slug ? (
    <a className="lb-last-win-link" href={`/locations/${row.last_win_location_slug}`}>
      {row.last_win_location}
    </a>
  ) : (
    <span>{row.last_win_location}</span>
  );
  return (
    <span className="lb-last-win">
      {name}
      {row.last_win_date && (
        <span className="lb-last-win-date">{formatDate(row.last_win_date)}</span>
      )}
    </span>
  );
}

function WeekLocations({ items }: { items?: WeekLocation[] }) {
  if (!items || items.length === 0) {
    return <span className="lb-zero">—</span>;
  }
  const shown = items.slice(0, WEEK_LOCATIONS_VISIBLE);
  const hidden = items.length - shown.length;
  return (
    <span className="lb-week-locations">
      {shown.map((item) => (
        <span key={`${item.name}-${item.slug ?? ""}`} className="lb-week-location">
          {/* Слаг может не резолвиться у площадок без внятного external_key —
              тогда просто текст, как и в «Последней победе». */}
          {item.slug ? (
            <a className="lb-last-win-link" href={`/locations/${item.slug}`}>
              {item.name}
            </a>
          ) : (
            <span>{item.name}</span>
          )}
          {item.date && (
            <span className="lb-last-win-date">{formatDate(item.date)}</span>
          )}
        </span>
      ))}
      {hidden > 0 && (
        <span
          className="lb-week-more"
          title={items
            .slice(WEEK_LOCATIONS_VISIBLE)
            .map((item) => item.name)
            .join(", ")}
        >
          +{hidden}
        </span>
      )}
    </span>
  );
}

function GeoCount({ value }: { value?: number | null }) {
  if (value == null) {
    return <span className="lb-zero">—</span>;
  }
  return <span className="lb-cell">{value}</span>;
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
      <a
        className="lb-name lb-name-link"
        href={`/users/${row.site_serial_id}`}
        onClick={(event) => event.stopPropagation()}
      >
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
  if (key === "best_time") {
    // Сортировка везде по убыванию, а лучшее время — наименьшее: инвертируем.
    // Строки без времени уходят в конец при любом направлении.
    const seconds = row.best_time_sec;
    return seconds != null && seconds > 0 ? -seconds : Number.NEGATIVE_INFINITY;
  }
  return row.total;
}

// Выбор ролей живёт в ссылке (ей можно поделиться) и в localStorage (чтобы не
// выбирать заново при каждом заходе). Ключ хранения — на рейтинг: в туризме и
// в счёте волонтёрств люди спорят о разном.
function roleStorageKey(metric: LeaderboardMetric): string {
  return `lbRoles:${metric}`;
}

function readStoredRolePreset(metric: LeaderboardMetric): VolunteerRolePreset {
  const fromUrl = new URLSearchParams(window.location.search).get("roles");
  if (
    fromUrl === "on_site" ||
    fromUrl === "on_site_no_run" ||
    fromUrl === "remote" ||
    fromUrl === "all"
  ) {
    return fromUrl;
  }
  if (fromUrl) {
    return "custom";
  }
  try {
    const raw = localStorage.getItem(roleStorageKey(metric));
    const parsed = raw ? (JSON.parse(raw) as { preset?: string }) : null;
    const preset = parsed?.preset;
    if (
      preset === "on_site" ||
      preset === "on_site_no_run" ||
      preset === "remote" ||
      preset === "custom"
    ) {
      return preset;
    }
  } catch {
    // повреждённое или недоступное хранилище — просто «все роли»
  }
  return "all";
}

function readStoredRoleKeys(metric: LeaderboardMetric): string[] {
  const fromUrl = new URLSearchParams(window.location.search).get("roles");
  const presetNames = ["all", "on_site", "on_site_no_run", "remote"];
  if (fromUrl && !presetNames.includes(fromUrl)) {
    return fromUrl.split(",").filter(Boolean);
  }
  if (fromUrl) {
    return [];
  }
  try {
    const raw = localStorage.getItem(roleStorageKey(metric));
    const parsed = raw ? (JSON.parse(raw) as { keys?: string[] }) : null;
    return Array.isArray(parsed?.keys) ? parsed.keys : [];
  } catch {
    return [];
  }
}

export function LeaderboardPage({ metric }: LeaderboardPageProps) {
  const [data, setData] = useState<LeaderboardResponse | null>(null);
  const [me, setMe] = useState<MyLeaderboardRow | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("rank");
  const [gender, setGender] = useState<LeaderboardGender>("all");
  const [minVisits, setMinVisits] = useState(1);
  const [platform, setPlatform] = useState<PlatformFilter>("all");
  const [countBy, setCountBy] = useState<CountBy>("locations");
  // Фильтр ролей: пресет + конкретные ключи. Стартовое значение — из ссылки
  // (можно кинуть в чат и спорить предметно), иначе из прошлого выбора
  // в этом браузере.
  const [rolePreset, setRolePreset] = useState<VolunteerRolePreset>(() =>
    readStoredRolePreset(metric),
  );
  const [roleKeys, setRoleKeys] = useState<string[]>(() => readStoredRoleKeys(metric));
  const [roleCatalog, setRoleCatalog] = useState<VolunteerRoleItem[]>([]);
  const [rolesModalOpen, setRolesModalOpen] = useState(false);
  const hasRoleFilter = ROLE_FILTER_METRICS.includes(metric);
  // В запрос уходят только реально выбранные роли; «все роли» — пустой список.
  const effectiveRoles = hasRoleFilter && rolePreset !== "all" ? roleKeys : null;
  const roleFilterKey = effectiveRoles ? [...effectiveRoles].sort().join(",") : "";

  const applyRoleFilter = useCallback(
    (preset: VolunteerRolePreset, keys: string[]) => {
      setRolePreset(preset);
      setRoleKeys(keys);
      setRolesModalOpen(false);
      try {
        localStorage.setItem(roleStorageKey(metric), JSON.stringify({ preset, keys }));
      } catch {
        // приватный режим — выбор проживёт до перезагрузки
      }
      // Ссылку правим без перезагрузки: у пресета читаемое имя, у своего
      // набора — перечень ролей, чтобы её можно было отправить в чат.
      const url = new URL(window.location.href);
      if (preset === "all") {
        url.searchParams.delete("roles");
      } else {
        url.searchParams.set("roles", preset === "custom" ? keys.join(",") : preset);
      }
      window.history.replaceState(null, "", url.toString());
    },
    [metric],
  );

  // Пресет из ссылки приходит именем — ключи ролей подставляем, когда доехал
  // справочник (своя копия разметки на фронте была бы вторым источником правды).
  useEffect(() => {
    if (!hasRoleFilter || roleCatalog.length === 0) {
      return;
    }
    const isPresetWithKeys =
      rolePreset === "on_site" || rolePreset === "on_site_no_run" || rolePreset === "remote";
    if (isPresetWithKeys && roleKeys.length === 0) {
      setRoleKeys(presetRoleKeys(rolePreset, roleCatalog) ?? []);
    }
  }, [hasRoleFilter, roleCatalog, rolePreset, roleKeys.length]);
  const [visibleCount, setVisibleCount] = useState(PAGE_STEP);
  // Развёрнутые строки мультиволонтёра (детализация «роль × система»).
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const [showScrollTop, setShowScrollTop] = useState(false);
  const myRowRef = useRef<HTMLTableRowElement | null>(null);
  const tableRef = useRef<HTMLTableElement | null>(null);
  const attachFloatingHead = useFloatingTableHead();
  // «Кратко | Полно» действует только на узких экранах; десктоп всегда полный.
  const [tableView, setTableView] = useTableView("leaderboard");
  const narrowViewport = useNarrowViewport();
  const showFull = !narrowViewport || tableView === "full";

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
  const hasMinVisits = MIN_VISITS_METRICS.includes(metric);
  const effectiveMinVisits = hasMinVisits ? minVisits : 1;
  // Победные рейтинги несут две дополнительные колонки: лучшее время (после
  // имени) и последнюю победу/новую локацию (в конце таблицы).
  const hasWinExtras = metric === "wins" || metric === "win_locations";
  const lastWinMeta = LAST_WIN_META[metric];
  const isRoles = metric === "volunteer_roles";
  // Гео-зачёт есть ровно там же, где порог визитов, — у туристических рейтингов.
  const effectiveCountBy = hasMinVisits ? countBy : "locations";

  useEffect(() => {
    if (!hasRoleFilter || roleCatalog.length > 0) {
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
        // без справочника просто не покажем модалку — рейтинг остаётся рабочим
      });
    return () => {
      cancelled = true;
    };
  }, [hasRoleFilter, roleCatalog.length]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [board, myRow] = await Promise.all([
        getLeaderboard(
          metric,
          1000,
          effectiveGender,
          effectiveMinVisits,
          platform,
          effectiveCountBy,
          effectiveRoles,
        ),
        getMyLeaderboardRow(
          metric,
          effectiveGender,
          effectiveMinVisits,
          platform,
          effectiveCountBy,
          effectiveRoles,
        ).catch(() => null),
      ]);
      setData(board);
      setMe(myRow);
      // Выбранная система могла оказаться неприменимой к этому рейтингу (parkrun
      // не участвует в волонтёрском туризме) — бэкенд тогда считает «все системы».
      // Сверяемся именно с тем, что запрашивали: сравнивать с состоянием на
      // каждый рендер нельзя, иначе ответ по прошлому фильтру откатывал бы
      // только что нажатую кнопку.
      if (board.platform && board.platform !== platform) {
        setPlatform(board.platform);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить рейтинг");
    } finally {
      setLoading(false);
    }
    // effectiveRoles — массив, поэтому в зависимости идёт его строковый ключ:
    // иначе новая ссылка на тот же набор гоняла бы запрос на каждый рендер.
  }, [
    metric,
    effectiveGender,
    effectiveMinVisits,
    platform,
    effectiveCountBy,
    roleFilterKey,
  ]);

  useEffect(() => {
    void load();
  }, [load]);

  // Метрика сменилась (напр. переход между рейтингами) — сбрасываем фильтры:
  // пол в «Абсолют», порог визитов в «от 1», систему в «Объединённо»,
  // единицу зачёта в «Локации».
  useEffect(() => {
    setGender("all");
    setMinVisits(1);
    setPlatform("all");
    setCountBy("locations");
  }, [metric]);

  useEffect(() => {
    setVisibleCount(PAGE_STEP);
    // Развёрнутые детализации закрываем: под новым фильтром это уже другой
    // набор строк, держать «раскрытым» уехавшее место незачем.
    setExpandedRows(new Set());
  }, [
    query,
    sortKey,
    effectiveGender,
    effectiveMinVisits,
    platform,
    effectiveCountBy,
    roleFilterKey,
  ]);

  const toggleRow = useCallback((key: string) => {
    setExpandedRows((current) => {
      const next = new Set(current);
      if (!next.delete(key)) {
        next.add(key);
      }
      return next;
    });
  }, []);

  const columns = data?.platform_columns ?? [];
  const platformOptions = data?.platform_options ?? [];
  const hasPlatformFilter = platformOptions.length > 1;
  // Набор кнопок «единицы зачёта» и наличие колонки «Последняя неделя» решает
  // бэкенд — фронт не повторяет правила, какие рейтинги их получают.
  const countByOptions = data?.count_by_options ?? [];
  const hasCountByFilter = countByOptions.length > 1;
  const hasGeoColumns = hasCountByFilter;
  const hasWeekLocations = data?.has_week_locations ?? false;
  // Таблицы с колонками-названиями (локации, роли, «последняя неделя») ведём в
  // жёсткой раскладке: в table-layout:auto колонка не может стать уже своего
  // самого длинного слова, и «Стрежевой Городской парк» раздувает всю таблицу.
  // Минимальная ширина у каждого набора колонок своя — отсюда три модификатора.
  const wideTableKind = hasWinExtras
    ? "lb-table-wins"
    : hasGeoColumns
      ? "lb-table-geo"
      : hasWeekLocations
        ? "lb-table-week"
        : "";
  const wideTable = wideTableKind !== "";
  const minVisitsHint =
    metric === "volunteer_locations" ? VOLUNTEER_MIN_VISITS_HINT : MIN_VISITS_HINT;

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
      best_time_sec: me.best_time_sec,
      best_time_display: me.best_time_display,
      last_win_location: me.last_win_location,
      last_win_location_slug: me.last_win_location_slug,
      last_win_date: me.last_win_date,
      top_role: me.top_role,
      top_role_count: me.top_role_count,
      role_details: me.role_details,
      locations_total: me.locations_total,
      cities_total: me.cities_total,
      regions_total: me.regions_total,
      week_locations: me.week_locations,
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
  // Место + участник + «всего» и, у победных рейтингов, лучшее время,
  // топ-локация (только wins), последняя победа и любимая роль («Мультиволонтёр»).
  // В кратком виде — только первые три: остальные колонки скрыты целиком.
  const totalColumns = showFull
    ? columns.length +
      3 +
      (hasWinExtras ? 2 : 0) +
      (metric === "wins" ? 1 : 0) +
      (isRoles ? 1 : 0) +
      (hasGeoColumns ? 2 : 0) +
      (hasWeekLocations ? 1 : 0)
    : 3;
  const visibleRows = rows.slice(0, visibleCount);
  const nextChunkEnd = Math.min(visibleCount + PAGE_STEP, rows.length);

  // Перцентиль осмысленнее абсолютного места: «опережаете 97 %» мотивирует
  // и 128-го, и 3128-го. entrants — все прошедшие порог рейтинга.
  const entrants = data?.entrants ?? 0;
  const percentileText =
    me?.included && me.rank != null && entrants > 1
      ? formatPercentile(me.rank, entrants)
      : null;

  // hint — значок «i» рядом с названием колонки: пояснение по наведению, как у
  // «Топ-локации». Свой title у значка перекрывает «Сортировать по этому
  // столбцу» с самого th, чтобы подсказки не наслаивались.
  const headerCell = (key: SortKey, label: string, className: string, hint?: string) => (
    <th
      key={key}
      className={`${className} lb-sortable${sortKey === key ? " lb-sorted" : ""}`}
      onClick={() => setSortKey(key)}
      title="Сортировать по этому столбцу"
    >
      {label}
      {hint && <InfoHint text={hint} />}
      {sortKey === key && <span className="lb-sort-mark" aria-hidden>▾</span>}
    </th>
  );

  return (
    <PortalSectionShell sidebar={{ active: "ratings" }}>
      <div className="lb-page">
        <nav className="lb-breadcrumb">
          <a href="/ratings">← Все рейтинги</a>
          <span aria-hidden> / </span>
          <span>
            {crumbs.section} · {crumbs.label}
          </span>
        </nav>

        {loading && !data && (
          <p className="muted">Считаем рейтинг… Первый расчёт может занять до минуты.</p>
        )}
        {error && (
          <div className="lb-error">
            <p>{error}</p>
            <button type="button" className="btn btn-sm" onClick={() => void load()}>
              Повторить
            </button>
          </div>
        )}

        {/* Пересчёт под новый фильтр держим на месте: старая таблица гаснет, но
            не исчезает — иначе кнопки прыгают, а первый (непрогретый) вариант
            фильтра оставляет пустой экран на десятки секунд. */}
        {data && (
          <div className={`lb-page-body${loading ? " lb-refreshing" : ""}`}>
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
                  </div>
                )}
                {(hasMinVisits || hasPlatformFilter || hasCountByFilter) && (
                  <div className="lb-filters-row">
                    {hasCountByFilter && (
                      <div className="lb-visits">
                        <span className="lb-visits-label">
                          Считаем <InfoHint text={COUNT_BY_HINT} />
                        </span>
                        <div
                          className="lb-gender-tabs"
                          role="group"
                          aria-label="Единица зачёта"
                        >
                          {countByOptions.map((value) => (
                            <button
                              key={value}
                              type="button"
                              aria-pressed={countBy === value}
                              className={`lb-gender-tab${countBy === value ? " lb-gender-tab-active" : ""}`}
                              onClick={() => setCountBy(value)}
                            >
                              {COUNT_BY_LABELS[value] ?? value}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                    {hasMinVisits && (
                      <div className="lb-visits">
                        <span className="lb-visits-label">
                          Локация засчитывается от <InfoHint text={minVisitsHint} />
                        </span>
                        <div
                          className="lb-gender-tabs"
                          role="group"
                          aria-label="Минимум визитов на локацию"
                        >
                          {MIN_VISITS_OPTIONS.map((value) => (
                            <button
                              key={value}
                              type="button"
                              aria-pressed={minVisits === value}
                              className={`lb-gender-tab${minVisits === value ? " lb-gender-tab-active" : ""}`}
                              onClick={() => setMinVisits(value)}
                            >
                              {value}+
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                    {hasPlatformFilter && (
                      <div className="lb-visits">
                        <span className="lb-visits-label">
                          Система <InfoHint text={PLATFORM_FILTER_HINT} />
                        </span>
                        <div
                          className="lb-gender-tabs"
                          role="group"
                          aria-label="Смотреть по системе"
                        >
                          {platformOptions.map((value) => (
                            <button
                              key={value}
                              type="button"
                              aria-pressed={platform === value}
                              className={`lb-gender-tab${platform === value ? " lb-gender-tab-active" : ""}`}
                              onClick={() => setPlatform(value)}
                            >
                              {PLATFORM_TAB_LABELS[value] ?? value}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                    {hasRoleFilter && roleCatalog.length > 0 && (
                      <div className="lb-visits lb-roles-filter">
                        <span className="lb-visits-label">
                          Роли <InfoHint text={ROLE_FILTER_HINT} />
                        </span>
                        <button
                          type="button"
                          className={`lb-roles-gear${rolePreset !== "all" ? " lb-roles-gear-active" : ""}`}
                          aria-label="Настроить, какие роли считать"
                          title="Какие роли считать"
                          onClick={() => setRolesModalOpen(true)}
                        >
                          <span aria-hidden="true">⚙</span>
                          <span className="lb-roles-gear-text">
                            {ROLE_PRESET_SHORT[rolePreset]}
                          </span>
                        </button>
                      </div>
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
                      {hasGeoColumns && (
                        <>
                          <span className="lb-me-value">
                            <span className="lb-me-platform">
                              Городов <InfoHint text={CITIES_HINT} />
                            </span>
                            <GeoCount value={me.cities_total} />
                          </span>
                          <span className="lb-me-value">
                            <span className="lb-me-platform">
                              Регионов <InfoHint text={REGIONS_HINT} />
                            </span>
                            <GeoCount value={me.regions_total} />
                          </span>
                        </>
                      )}
                      {isRoles && me.top_role && (
                        <span className="lb-me-value">
                          <span className="lb-me-platform">
                            Любимая роль <InfoHint text={TOP_ROLE_HINT} />
                          </span>
                          <TopRole row={me} />
                        </span>
                      )}
                      {hasWinExtras && me.best_time_sec != null && (
                        <span className="lb-me-value">
                          <span className="lb-me-platform">
                            Лучшее время <InfoHint text={BEST_TIME_HINT} />
                          </span>
                          <BestTime row={me} />
                        </span>
                      )}
                      {metric === "wins" && me.home_location && (
                        <span className="lb-me-value">
                          <span className="lb-me-platform">
                            Топ-локация <InfoHint text={TOP_LOCATION_HINT} />
                          </span>
                          <TopWinLocation row={me} />
                        </span>
                      )}
                      {hasWinExtras && lastWinMeta && me.last_win_location && (
                        <span className="lb-me-value">
                          <span className="lb-me-platform">
                            {lastWinMeta.label} <InfoHint text={lastWinMeta.hint} />
                          </span>
                          <LastWinLocation row={me} />
                        </span>
                      )}
                      {hasWeekLocations && (me.week_locations?.length ?? 0) > 0 && (
                        <span className="lb-me-value lb-me-value-wide">
                          <span className="lb-me-platform">
                            Последняя неделя{" "}
                            <InfoHint text={WEEK_LOCATIONS_HINT[metric] ?? ""} />
                          </span>
                          <WeekLocations items={me.week_locations} />
                        </span>
                      )}
                    </span>
                    {/* Кнопка и перцентиль — одна связка: приписка встаёт справа
                        от кнопки и переносится вместе с ней, а не занимает
                        отдельную строку высотой в целый ряд. */}
                    {(myIndex >= 0 || percentileText != null) && (
                      <div className="lb-me-actions">
                        {myIndex >= 0 && (
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={showMyRow}
                          >
                            Показать в таблице
                          </button>
                        )}
                        {percentileText != null && (
                          <p className="lb-me-percentile muted">{percentileText}</p>
                        )}
                      </div>
                    )}
                  </div>
                ) : (
                  <p className="lb-me-threshold">
                    Вы появитесь в рейтинге после достижения {me.threshold}{" "}
                    {unitLabel(metric, me.threshold, effectiveCountBy)} — сейчас у вас{" "}
                    {me.total}.
                  </p>
                )}
              </section>
            )}

            <TableViewToggle value={tableView} onChange={setTableView} />
            <TableWrap
              innerRef={attachFloatingHead}
              className={`lb-table-wrap${wideTable ? " lb-table-wrap-wide" : ""}`}
            >
              <table
                ref={tableRef}
                className={`data-table lb-table${wideTable ? ` lb-table-fixed ${wideTableKind}` : ""}${
                  showFull ? " lb-table-full" : ""
                }`}
              >
                <thead>
                  <tr>
                    {headerCell("rank", "Место", "lb-col-rank")}
                    <th className="lb-col-name">Участник</th>
                    {showFull &&
                      hasWinExtras &&
                      headerCell("best_time", "Лучшее время", "lb-col-time", BEST_TIME_HINT)}
                    {/* Колонки систем не сортируются — для «посмотреть одну
                        систему» есть фильтр «Система» выше, он пересчитывает
                        место и «Всего», а не переставляет строки по столбцу. */}
                    {showFull &&
                      columns.map((code) => (
                        <th key={code} className="lb-col-num">
                          {PLATFORM_LABELS[code] ?? code}
                        </th>
                      ))}
                    {headerCell(
                      "total",
                      "Всего",
                      "lb-col-num lb-col-total",
                      isRoles ? ROLES_TOTAL_HINT : undefined,
                    )}
                    {showFull && hasGeoColumns && (
                      <>
                        <th className="lb-col-num lb-col-geo">
                          Городов <InfoHint text={CITIES_HINT} />
                        </th>
                        <th className="lb-col-num lb-col-geo">
                          Регионов <InfoHint text={REGIONS_HINT} />
                        </th>
                      </>
                    )}
                    {showFull && isRoles && (
                      <th className="lb-col-home">
                        Любимая роль <InfoHint text={TOP_ROLE_HINT} />
                      </th>
                    )}
                    {showFull && metric === "wins" && (
                      <th className="lb-col-home">
                        Топ-локация <InfoHint text={TOP_LOCATION_HINT} />
                      </th>
                    )}
                    {showFull && hasWinExtras && lastWinMeta && (
                      <th className="lb-col-last-win">
                        {lastWinMeta.label} <InfoHint text={lastWinMeta.hint} />
                      </th>
                    )}
                    {showFull && hasWeekLocations && (
                      <th className="lb-col-last-win">
                        Последняя неделя{" "}
                        <InfoHint text={WEEK_LOCATIONS_HINT[metric] ?? ""} />
                      </th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.map((row, index) => {
                    const isMe = me != null && row.site_serial_id === me.site_serial_id;
                    const rowKey = `${row.rank}-${row.display_name}-${row.site_serial_id ?? index}`;
                    const expanded = expandedRows.has(rowKey);
                    const rowClass = [
                      isMe ? "lb-row-me" : "",
                      isRoles ? "lb-row-expandable" : "",
                      expanded ? "lb-row-expanded" : "",
                    ]
                      .filter(Boolean)
                      .join(" ");
                    return (
                      <Fragment key={rowKey}>
                      <tr
                        ref={isMe ? myRowRef : undefined}
                        className={rowClass || undefined}
                        // Клик по строке разворачивает детализацию; клик по имени
                        // остаётся переходом в профиль (ссылка гасит всплытие).
                        onClick={isRoles ? () => toggleRow(rowKey) : undefined}
                      >
                        <td className="lb-col-rank">
                          <span className="lb-rank">
                            {isRoles && (
                              <button
                                type="button"
                                className="lb-expand-toggle"
                                aria-expanded={expanded}
                                aria-label={
                                  expanded ? "Свернуть роли" : "Показать роли участника"
                                }
                                onClick={(event) => {
                                  event.stopPropagation();
                                  toggleRow(rowKey);
                                }}
                              >
                                <span aria-hidden>{expanded ? "▾" : "▸"}</span>
                              </button>
                            )}
                            {row.rank}
                            <RankDelta delta={row.rank_delta} />
                          </span>
                        </td>
                        <td className="lb-col-name">
                          <ParticipantName row={row} />
                        </td>
                        {showFull && hasWinExtras && (
                          <td className="lb-col-time">
                            <BestTime row={row} />
                          </td>
                        )}
                        {showFull &&
                          columns.map((code) => (
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
                        {showFull && hasGeoColumns && (
                          <>
                            <td className="lb-col-num lb-col-geo">
                              <GeoCount value={row.cities_total} />
                            </td>
                            <td className="lb-col-num lb-col-geo">
                              <GeoCount value={row.regions_total} />
                            </td>
                          </>
                        )}
                        {showFull && isRoles && (
                          <td className="lb-col-home">
                            <TopRole row={row} />
                          </td>
                        )}
                        {showFull && metric === "wins" && (
                          <td className="lb-col-home">
                            <TopWinLocation row={row} />
                          </td>
                        )}
                        {showFull && hasWinExtras && lastWinMeta && (
                          <td className="lb-col-last-win">
                            <LastWinLocation row={row} />
                          </td>
                        )}
                        {showFull && hasWeekLocations && (
                          <td className="lb-col-last-win">
                            <WeekLocations items={row.week_locations} />
                          </td>
                        )}
                      </tr>
                      {isRoles && expanded && (
                        <tr className="lb-detail-row">
                          <td colSpan={totalColumns}>
                            <div className="lb-roles-detail">
                              <p className="lb-roles-caption muted">
                                Роли участника и число волонтёрств в каждой системе.
                                «Всего» в таблице — сколько разных ролей освоено, а не
                                сумма волонтёрств.
                              </p>
                              <RoleBreakdown
                                details={row.role_details ?? []}
                                columns={columns}
                              />
                            </div>
                          </td>
                        </tr>
                      )}
                      </Fragment>
                    );
                  })}
                  {visibleRows.length === 0 && (
                    <tr>
                      <td colSpan={totalColumns} className="muted">
                        Ничего не найдено
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </TableWrap>
            {/* Во время поиска бар не показываем: список короткий, а строка
                внизу закрывала как раз того участника, которого ищут. */}
            {me?.included && me.rank != null && !query.trim() && (
              <PinnedMeBar
                rowRef={myRowRef}
                tableRef={tableRef}
                rank={me.rank}
                name={me.display_name ?? "Вы"}
                value={me.total}
                onShow={showMyRow}
                watchKey={`${visibleRows.length}:${sortKey}:${query}`}
              />
            )}
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
          </div>
        )}
        {rolesModalOpen && roleCatalog.length > 0 && (
          <VolunteerRolesModal
            roles={roleCatalog}
            preset={rolePreset}
            selected={
              rolePreset === "all" ? roleCatalog.map((role) => role.key) : roleKeys
            }
            onApply={applyRoleFilter}
            onClose={() => setRolesModalOpen(false)}
          />
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
