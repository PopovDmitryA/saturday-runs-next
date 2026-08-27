import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import {
  ADMIN_ONLY_METRICS,
  COUNT_BY_LABELS,
  COUNT_BY_TOTAL_LABELS,
  GENDERED_METRICS,
  getLeaderboard,
  getMyLeaderboardRow,
  getVolunteerRoleCatalog,
  presetRoleKeys,
  ROLE_FILTER_METRICS,
  type VolunteerRoleItem,
  type VolunteerRolePreset,
  METRIC_VALUE_UNIT,
  MIN_VISITS_METRICS,
  MIN_VISITS_OPTIONS,
  PLATFORM_LABELS,
  TOURIST_MAP_METRICS,
  type CountBy,
  type LeaderboardGender,
  type LeaderboardMetric,
  type LeaderboardResponse,
  type LeaderboardRow,
  type MyLeaderboardRow,
  type PlatformFilter,
  type TouristMapVisit,
  type VolunteerRoleDetail,
  type WeekLocation,
} from "./leaderboardsApi";
import {
  COUNT_FORMS,
  formatDateTime,
  formatInt,
  pluralFormRu,
  pluralizeRu,
} from "../../lib/format";
import { useOptionalUser } from "../../lib/useOptionalUser";
import { NotFoundPage } from "../NotFoundPage";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { useOptionalShareSheet } from "../sharing/ShareSheetContext";
import { ratingSubject } from "../sharing/subjects";
import { formatFinishTime } from "./formatFinishTime";
import { unitLabel } from "./pluralize";
import { RatingsLoginBanner } from "./RatingsLoginBanner";
import { TouristMapPanel } from "./TouristMapPanel";
import { useTouristMap } from "./useTouristMap";
import { VolunteerRolesModal } from "./VolunteerRolesModal";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { TableViewToggle } from "../../components/tableUx/TableViewToggle";
import { useTableColumns } from "../../components/tableUx/useTableColumns";
import type { AdaptiveColumn } from "../../components/tableUx/useAdaptiveColumns";
import { PinnedMeBar } from "../../components/tableUx/PinnedMeBar";
import "./leaderboards.css";

const PAGE_STEP = 100;
const SCROLL_TOP_THRESHOLD = 480;

// Мужского зачёта нет: у мужчин «первое место среди мужчин» завышалось на
// стартах, где протокол не знает пол части финишёров (см. LeaderboardGender).
const GENDER_TABS: { value: LeaderboardGender; label: string }[] = [
  { value: "all", label: "Абсолют" },
  { value: "female", label: "Женщины" },
];

type LeaderboardPageProps = {
  metric: LeaderboardMetric;
};

// Колонки-системы больше не сортируются: чтобы посмотреть зачёт одной системы,
// есть фильтр «Система» — он пересчитывает и место, и «Всего», а не просто
// переставляет строки по одному столбцу (решение Дмитрия 01.08.2026).
type SortKey = "rank" | "total" | "best_time" | "remaining" | `light:${string}`;

/** Фильтр столбца светофоров: показать только побывавших или только тех, кто нет. */
type LightFilter = "yes" | "no";

/** Ключ площадки из ключа сортировки «light:...» (null — сортировка обычная). */
function sortedLightKey(key: SortKey): string | null {
  return key.startsWith("light:") ? key.slice("light:".length) : null;
}

const METRIC_CRUMBS: Record<LeaderboardMetric, { section: string; label: string }> = {
  runs: { section: "Бегуны", label: "Количество пробежек" },
  volunteering: { section: "Волонтёры", label: "Количество волонтёрств" },
  volunteer_roles: { section: "Волонтёры", label: "Мультиволонтёр" },
  locations: { section: "Паркран-туристы", label: "Уникальные локации" },
  volunteer_locations: { section: "Волонтёры", label: "Уникальные локации" },
  openings: { section: "Паркран-туристы", label: "Открытия локаций" },
  wins: { section: "Бегуны", label: "Количество первых мест" },
  win_locations: { section: "Паркран-туристы", label: "Локации с первым местом" },
  home_distance: { section: "Паркран-туристы", label: "Дальность от дома" },
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

const HOME_LOCATION_HINT =
  "Локация, от которой считаются километры: где у участника больше всего " +
  "пробежек. Зарегистрированные на сайте могут выбрать её вручную в настройках.";

const HOME_FILTER_HINT =
  "«Только явный дом» убирает участников, у которых домашняя локация выбрана " +
  "автоматически из нескольких почти равных локаций: нулевая точка у них " +
  "условна, и километры зависят от того, какую локацию выбрал алгоритм.";

const HOME_DISTANCE_LOCATIONS_HINT =
  "Сколько разных локаций посещено, включая домашнюю. Километры каждой " +
  "засчитываются один раз — повторные поездки сумму не увеличивают.";

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
  "Сколько РАЗНЫХ городов набрано засчитанными локациями: несколько парков " +
  "одного города дают один город.";

// Прогноз завершения туризма — перенос дашборда Grafana «Прогноз даты
// завершения туризма» (просьба Дмитрия 27.08.2026). Знаменатель остатка — те
// же действующие площадки каталога, что показывает карта.
const REMAINING_WHAT: Record<CountBy, string> = {
  locations: "действующих локаций",
  cities: "городов с действующими локациями",
  regions: "регионов с действующими локациями",
};

function remainingHint(countBy: CountBy): string {
  return (
    `Сколько ${REMAINING_WHAT[countBy]} участник ещё не закрыл. Считаются ` +
    "действующие площадки 5 вёрст, С95 и RunPark; закрытые, «скоро» и весь " +
    "parkrun в остаток не идут — приехать туда уже нельзя."
  );
}

const FORECAST_HINT =
  "Когда туризм закончится, если с ближайшего старта брать по новой локации " +
  "каждую субботу: в расписании учтены бонусные старты 1 января (их два) и " +
  "12 июня. Пропуск сдвигает дату, новая площадка в каталоге — тоже.";

const REGIONS_HINT =
  "Сколько РАЗНЫХ регионов набрано засчитанными локациями. Зарубежные старты " +
  "считаются по стране: одна страна — один регион.";

// Колонка «Последняя неделя»: ОДНА площадка и дата — тем же видом, что
// «Последняя победа» в победных рейтингах. Показывает последний старт за окно
// дельты, независимо от того, дал он +1 или нет: в туризме повторный заезд на
// давно освоенную площадку в «Всего» не идёт, но в колонке виден (решение
// Дмитрия 02.08.2026). Не был нигде — прочерк.
const WEEK_LOCATIONS_HINT: Record<string, string> = {
  runs: "Последний старт участника за неделю.",
  volunteering: "Последнее волонтёрство участника за неделю.",
  locations:
    "Последний старт участника за неделю. Знакомая локация «Всего» не " +
    "увеличивает — новых локаций она не добавляет, — но в колонке видна.",
  volunteer_locations:
    "Последнее волонтёрство участника за неделю. Знакомая локация «Всего» не " +
    "увеличивает — новых локаций она не добавляет, — но в колонке видна.",
  home_distance:
    "Последний старт участника за неделю. Знакомая локация километров не " +
    "добавляет — её зачёт уже учтён, — но в колонке видна.",
};

const VISIT_LIGHT_HINT =
  "Светофор по выбранной на карте локации: зелёный — был, красный — не был. " +
  "Наведите (или коснитесь) — покажем системы и даты.";

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
  // Та же колонка «локация + дата» у рейтинга открытий: последнее открытие,
  // на котором человек бежал.
  openings: {
    label: "Последнее открытие",
    hint: "Локация, на открытии которой участник был последний раз, и дата этого старта.",
  },
};

function formatBestTime(row: {
  best_time_sec?: number | null;
  best_time_display?: string | null;
}): string | null {
  return formatFinishTime(row.best_time_sec, row.best_time_display);
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
  return `Вы опережаете ${ahead} % из ${pluralizeRu(entrants, COUNT_FORMS.participants)} рейтинга`;
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
  row: {
    home_location?: string | null;
    home_location_slug?: string | null;
    home_location_wins?: number | null;
  };
}) {
  if (!row.home_location) {
    return <span className="lb-zero">—</span>;
  }
  return (
    <span className="lb-home">
      <HomeLocationName row={row} />
      {row.home_location_wins != null && row.home_location_wins > 1 && (
        <span className="lb-home-count"> ×{row.home_location_wins}</span>
      )}
    </span>
  );
}

// Домашняя локация в рейтинге дальности задаёт нулевую точку для всех
// километров строки, поэтому спорный выбор помечаем прямо в таблице. Два
// уровня (решение Дмитрия 03.08.2026): ручной выбор вне тройки — красное
// предупреждение, неоднозначный автовыбор — мягкая янтарная пометка.
const HOME_NOTE_META: Record<string, { level: "warn" | "danger"; hint: string }> = {
  ambiguous: {
    level: "warn",
    hint: "Домашняя локация определена автоматически: у участника несколько локаций с близким числом пробежек.",
  },
  manual_off_top: {
    level: "danger",
    hint: "Домашняя локация указана вручную и не входит в топ-3 площадок участника по числу пробежек.",
  },
};

// Название домашней/топ-локации — ссылкой на её страницу: внутренняя
// перелинковка передаёт страницам локаций вес с посещаемых рейтингов
// (решение Дмитрия 06.08.2026), а участнику даёт короткий путь к площадке.
function HomeLocationName({
  row,
}: {
  row: { home_location?: string | null; home_location_slug?: string | null };
}) {
  if (row.home_location_slug) {
    return (
      <a className="lb-last-win-link" href={`/locations/${row.home_location_slug}`}>
        {row.home_location}
      </a>
    );
  }
  return <>{row.home_location}</>;
}

function HomeLocationCell({
  row,
}: {
  row: {
    home_location?: string | null;
    home_location_slug?: string | null;
    home_location_note?: string | null;
  };
}) {
  if (!row.home_location) {
    return <span className="lb-zero">—</span>;
  }
  const note = row.home_location_note ? HOME_NOTE_META[row.home_location_note] : null;
  return (
    <span className="lb-home">
      <HomeLocationName row={row} />
      {note && (
        <StatHintTooltip text={note.hint}>
          <span
            className={`lb-home-warn lb-home-warn-${note.level}`}
            aria-label={note.hint}
          >
            !
          </span>
        </StatHintTooltip>
      )}
    </span>
  );
}

/**
 * Смена домашней локации доходит до таблицы не сразу: строки приходят из
 * снапшота с TTL в несколько часов, а строка «Вы» считается вживую по текущим
 * настройкам. Человек, только что переехавший, видит в таблице километры от
 * прежней площадки и читает это как ошибку рейтинга — плашка объясняет разрыв
 * и называет, когда он закроется (задача Дмитрия 07.08.2026).
 *
 * Показывается только тому, кто действительно менял дом позже, чем был посчитан
 * снапшот: остальным про пересчёт достаточно строки в шапке рейтинга.
 */
function HomeChangeNotice({
  changedAt,
  builtAt,
  refreshHours,
  includedInTable,
}: {
  changedAt: string;
  builtAt: string | null;
  refreshHours: number;
  includedInTable: boolean;
}) {
  const builtMs = builtAt ? Date.parse(builtAt) : Number.NaN;
  const etaMs = Number.isNaN(builtMs) ? null : builtMs + refreshHours * 3600 * 1000;
  // ETA в прошлом (снапшот пережил TTL) — не обещаем момент, которого уже нет.
  const eta = etaMs != null && etaMs > Date.now() ? new Date(etaMs).toISOString() : null;
  return (
    <section className="lb-home-change" role="status">
      <span className="lb-home-change-icon" aria-hidden="true">
        ⏳
      </span>
      <div className="lb-home-change-text">
        <p>
          <strong>Вы сменили домашнюю локацию {formatDateTime(changedAt)}.</strong> Таблица
          ниже посчитана раньше, поэтому в её строках пока километры от прежнего дома.
        </p>
        <p>
          Пересчёт автоматический:{" "}
          {eta
            ? `новые километры появятся в таблице примерно к ${formatDateTime(eta)}`
            : `новые километры появятся в таблице не позже чем через ${pluralizeRu(refreshHours, ["час", "часа", "часов"])}`}
          .{includedInTable && " Ваша строка «Вы» уже считает от новой домашней локации."}
        </p>
      </div>
    </section>
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
                  formatInt(detail.platforms[code])
                ) : (
                  <span className="lb-zero">—</span>
                )}
              </td>
            ))}
            <td className="lb-col-num lb-col-total">{formatInt(detail.total)}</td>
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

function WeekLocationCell({ item }: { item?: WeekLocation | null }) {
  if (!item) {
    return <span className="lb-zero">—</span>;
  }
  return (
    <span className="lb-week-locations">
      <span className="lb-week-location">
        {/* Слаг может не резолвиться у площадок без внятного external_key —
            тогда просто текст, как и в «Последней победе». */}
        {item.slug ? (
          <a className="lb-last-win-link" href={`/locations/${item.slug}`}>
            {item.name}
          </a>
        ) : (
          <span>{item.name}</span>
        )}
        {item.date && <span className="lb-last-win-date">{formatDate(item.date)}</span>}
      </span>
    </span>
  );
}

function ForecastDate({
  remaining,
  date,
}: {
  remaining?: number | null;
  date?: string | null;
}) {
  // Остаток ноль — квест закрыт: даты у такой строки нет и быть не может.
  if (remaining === 0) {
    return (
      <span className="lb-forecast-done" title="Все действующие локации уже закрыты">
        всё пройдено
      </span>
    );
  }
  if (!date) {
    return <span className="lb-zero">—</span>;
  }
  return <span className="lb-cell lb-forecast">{formatDate(date)}</span>;
}

function GeoCount({ value }: { value?: number | null }) {
  if (value == null) {
    return <span className="lb-zero">—</span>;
  }
  return <span className="lb-cell">{formatInt(value)}</span>;
}

function formatDate(value: string | null): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("ru-RU");
}

/**
 * Светофор «был здесь»: зелёный — был, красный — не был (решение Дмитрия
 * 15.08.2026: порог визитов рейтинга сюда не примешиваем — был значит был).
 * Подсказка (и по наведению, и по тапу) называет системы и даты — иначе
 * зелёная точка ничего не рассказывает.
 */
function VisitLight({
  visit,
  covered,
  locationName,
  limit,
}: {
  visit?: TouristMapVisit;
  covered: boolean;
  locationName: string;
  limit: number;
}) {
  if (!covered) {
    // Строка вне расчёта — это дописанная в конец «моя» строка из-за топ-1000
    // таблицы: у неё нет якоря в снапшоте, и визитов мы не знаем.
    return (
      <StatHintTooltip
        text={`Светофоры считаем по строкам таблицы (топ-${limit}) — эта строка дописана сверх неё.`}
        className="lb-light-wrap"
      >
        <span className="lb-light lb-light-unknown" aria-label="Нет данных">
          —
        </span>
      </StatHintTooltip>
    );
  }
  if (!visit) {
    return (
      <StatHintTooltip text={`Не был: ${locationName}`} className="lb-light-wrap">
        <span className="lb-light lb-light-no" role="img" aria-label={`Не был: ${locationName}`} />
      </StatHintTooltip>
    );
  }
  const content = (
    <span className="lb-light-hint">
      <b>{locationName}</b>
      {visit.platforms.map((platform) => (
        <span key={platform.code}>
          {PLATFORM_LABELS[platform.code] ?? platform.code}:{" "}
          {formatInt(platform.visits)}{" "}
          {pluralFormRu(platform.visits, ["визит", "визита", "визитов"])} —{" "}
          {formatDate(platform.first_date)}
          {platform.last_date && platform.last_date !== platform.first_date
            ? ` … ${formatDate(platform.last_date)}`
            : ""}
        </span>
      ))}
    </span>
  );
  return (
    <StatHintTooltip content={content} className="lb-light-wrap">
      <span
        className="lb-light lb-light-yes"
        role="img"
        aria-label={`Был: ${locationName}`}
      />
    </StatHintTooltip>
  );
}

function DeltaSlot({ delta }: { delta: number }) {
  // Слот фиксированной ширины: дельта не сдвигает цифры в колонке.
  return <span className="lb-delta">{delta > 0 ? `+${formatInt(delta)}` : ""}</span>;
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

/** Единица измерения мелким шрифтом сразу за числом: «126 789 км». */
function Unit({ unit }: { unit?: string }) {
  if (!unit) {
    return null;
  }
  return <span className="lb-unit">{unit}</span>;
}

function CellValue({
  cell,
  unit,
}: {
  cell?: { value: number; delta: number };
  unit?: string;
}) {
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
      {formatInt(cell.value)}
      <Unit unit={unit} />
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
  if (key === "remaining") {
    // «Осталось» интересно с малого конца — кто ближе всех к финишу квеста,
    // поэтому инвертируем, как у лучшего времени. Прогноз по дате сортировать
    // отдельно незачем: он растёт ровно с остатком.
    const left = row.remaining_total;
    return left != null ? -left : Number.NEGATIVE_INFINITY;
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

/**
 * Ещё не открытый рейтинг ведёт себя как несуществующий адрес: API отдаёт
 * «неизвестный рейтинг» всем, кроме админа, и страница показывает то же самое.
 * Пока сессия проверяется, не мигаем «404» перед админом.
 */
export function LeaderboardPage({ metric }: LeaderboardPageProps) {
  const viewer = useOptionalUser();
  if (ADMIN_ONLY_METRICS.includes(metric)) {
    if (viewer === undefined) {
      return null;
    }
    if (!viewer?.is_admin) {
      return <NotFoundPage />;
    }
  }
  return <LeaderboardBoard metric={metric} />;
}

function LeaderboardBoard({ metric }: LeaderboardPageProps) {
  const shareSheet = useOptionalShareSheet();
  const currentUser = useOptionalUser();
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
  // Фильтр «только очевидный дом» — у рейтинга дальности: прячет участников,
  // у которых нулевая точка выбрана автоматически из почти равных площадок.
  const [hideAmbiguousHome, setHideAmbiguousHome] = useState(false);
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
  // Спойлер «Карта туристов» — только у туристических рейтингов и только по
  // раскрытию: карта тянет каталог локаций и свою матрицу, грузить их всем
  // подряд ради блока, который открывают не всегда, незачем.
  const hasTouristMap = TOURIST_MAP_METRICS.includes(metric);
  const [mapOpen, setMapOpen] = useState(false);
  // Развёрнутые строки мультиволонтёра (детализация «роль × система»).
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const [showScrollTop, setShowScrollTop] = useState(false);
  const myRowRef = useRef<HTMLTableRowElement | null>(null);
  const tableRef = useRef<HTMLTableElement | null>(null);
  const attachFloatingHead = useFloatingTableHead(".tview-bar");

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
  // «Дальность от дома» несёт две свои колонки: домашняя локация и сколько
  // площадок посещено — без них число километров ни о чём не говорит.
  const isHomeDistance = metric === "home_distance";
  // Без подписи колонка «Всего» у дальности читается как счётчик пробежек.
  const valueUnit = METRIC_VALUE_UNIT[metric];
  // Гео-зачёт есть ровно там же, где порог визитов, — у туристических рейтингов.
  const effectiveCountBy = hasMinVisits ? countBy : "locations";

  // Карта туристов живёт под теми же фильтрами, что таблица: числа у точек и
  // светофоры обязаны считаться так же, как места в рейтинге.
  const touristMap = useTouristMap({
    metric,
    enabled: hasTouristMap && mapOpen,
    minVisits: effectiveMinVisits,
    platform,
    countBy: effectiveCountBy,
    roles: effectiveRoles,
  });
  const lightColumns = hasTouristMap ? touristMap.selected : [];
  const showLights = lightColumns.length > 0;
  // Фильтр по столбцу светофоров: ключ площадки -> «только был» / «только не был».
  // Отсутствие ключа означает «показывать всех».
  const [lightFilters, setLightFilters] = useState<Record<string, LightFilter>>({});
  // Направление сортировки по столбцу карты. Повторный клик по заголовку
  // переворачивает: сначала побывавшие — сначала те, кто не был.
  const [lightSortMissingFirst, setLightSortMissingFirst] = useState(false);
  const lightColumnsKey = lightColumns.map(({ location }) => location.key).join("|");

  // Обновления состояний идут рядом, а не вложенно: updater у setState в
  // StrictMode вызывается дважды, и переключатель направления внутри него
  // срабатывал бы два раза — порядок возвращался к исходному.
  const sortByLightColumn = useCallback(
    (columnKey: SortKey) => {
      if (sortKey === columnKey) {
        setLightSortMissingFirst((value) => !value);
        return;
      }
      setSortKey(columnKey);
      setLightSortMissingFirst(false);
    },
    [sortKey],
  );

  const cycleLightFilter = useCallback((key: string) => {
    // Все → только был → только не был → снова все.
    setLightFilters((current) => {
      const next = { ...current };
      if (!next[key]) {
        next[key] = "yes";
      } else if (next[key] === "yes") {
        next[key] = "no";
      } else {
        delete next[key];
      }
      return next;
    });
  }, []);

  // Столбец убрали — забываем его фильтр и снимаем сортировку по нему, иначе
  // таблица осталась бы отфильтрованной по невидимой площадке.
  useEffect(() => {
    const alive = new Set(lightColumnsKey ? lightColumnsKey.split("|") : []);
    setLightFilters((current) => {
      const next = Object.fromEntries(
        Object.entries(current).filter(([key]) => alive.has(key)),
      );
      return Object.keys(next).length === Object.keys(current).length ? current : next;
    });
    setSortKey((current) => {
      const sorted = sortedLightKey(current);
      return sorted && !alive.has(sorted) ? "rank" : current;
    });
  }, [lightColumnsKey]);

  // «Посмотреть людей в таблице» из попапа карты: столбец уже добавлен, дело за
  // прокруткой. Шапка таблицы липкая, поэтому целимся чуть выше её верха.
  const scrollToTable = useCallback(() => {
    window.setTimeout(() => {
      const table = tableRef.current;
      if (!table) {
        return;
      }
      const top = table.getBoundingClientRect().top + window.scrollY - 120;
      window.scrollTo({ top, behavior: "smooth" });
    }, 60);
  }, []);

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
          hideAmbiguousHome,
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
    hideAmbiguousHome,
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
    lightFilters,
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
  // Прогноз завершения туризма: колонки «Осталось» и «Прогноз». Есть ли они у
  // этого варианта рейтинга — решает бэкенд (в зачёте parkrun прогноза нет).
  const hasForecast = data?.has_forecast ?? false;
  // В туризме «Всего» называет единицу зачёта («Всего городов»): рядом стоят
  // столбцы «Городов» и «Регионов», и без уточнения непонятно, что в итоге.
  const totalLabel = hasCountByFilter
    ? COUNT_BY_TOTAL_LABELS[effectiveCountBy]
    : "Всего";
  // Таблицы с колонками-названиями (локации, роли, «последняя неделя») ведём в
  // жёсткой раскладке: в table-layout:auto колонка не может стать уже своего
  // самого длинного слова, и «Стрежевой Городской парк» раздувает всю таблицу.
  // Минимальная ширина у каждого набора колонок своя — отсюда три модификатора.
  const wideTableKind = hasWinExtras
    ? "lb-table-wins"
    : hasGeoColumns
      ? "lb-table-geo"
      : hasWeekLocations || lastWinMeta
        ? // Открытия: колонок ровно столько же, что у «недели» (системы, «Всего»
          // и одна колонка с названием площадки), поэтому и раскладка та же.
          "lb-table-week"
        : "";
  const wideTable = wideTableKind !== "";
  // Фильтр «по системе» убирает колонки-системы целиком, и жёсткая раскладка
  // набора становится шире фактической таблицы. Модификатор возвращает
  // min-width к реальной сумме колонок (см. .lb-table-no-platforms).
  const noPlatformColumns = columns.length === 0;

  // Единый механизм со всеми таблицами сайта: краткий вид набирает колонки под
  // ширину блока. Порядок — по важности, а не по выводу; колонки систем стоят
  // последними: это самая тяжёлая часть таблицы (четыре числовых столбца), и
  // именно её раньше прятал краткий вид на любой ширине.
  const lbColumns = useMemo<AdaptiveColumn[]>(() => {
    // Дальность меряется в километрах: в ячейке «365 821 км» с дельтой, и
    // числовые колонки там заметно шире обычных (см. .lb-table-wide-values).
    // Оценка ширины обязана совпадать с вёрсткой, иначе краткий вид считает,
    // что полный набор влезает, а тот уезжает в горизонтальный скролл.
    const numWidth = isHomeDistance ? 140 : 76;
    const list: AdaptiveColumn[] = [
      { key: "rank", width: 88, required: true },
      { key: "name", width: 200, required: true },
      { key: "total", width: isHomeDistance ? 144 : 112, required: true },
    ];
    // Светофоры выбранных площадок — ради них на карту и нажимали, поэтому
    // краткий вид их не прячет.
    for (const { location } of lightColumns) {
      list.push({ key: `light:${location.key}`, width: 108, required: true });
    }
    if (hasWinExtras) {
      list.push({ key: "best_time", width: 112 });
    }
    if (hasGeoColumns) {
      list.push({ key: "geo", width: 160 });
    }
    // «Осталось» + «Прогноз» идут одной группой: дата без остатка не читается,
    // а остаток без даты — половина ответа. Ширина — сумма их колонок в CSS
    // (5.2rem + 5.8rem), иначе краткий вид ошибётся в том, что влезло.
    if (hasForecast) {
      list.push({ key: "forecast", width: 176 });
    }
    if (isHomeDistance) {
      list.push({ key: "home", width: 232 });
    }
    if (isRoles) {
      list.push({ key: "top_role", width: 168 });
    }
    if (metric === "wins") {
      list.push({ key: "top_location", width: 168 });
    }
    if (lastWinMeta) {
      list.push({ key: "last_win", width: 176 });
    }
    if (hasWeekLocations) {
      list.push({ key: "week", width: 176 });
    }
    if (columns.length > 0) {
      list.push({ key: "platforms", width: columns.length * numWidth });
    }
    return list;
  }, [
    columns.length,
    hasForecast,
    hasGeoColumns,
    hasWeekLocations,
    hasWinExtras,
    isHomeDistance,
    isRoles,
    lastWinMeta,
    metric,
    lightColumns,
  ]);

  const tableColumns = useTableColumns(lbColumns);
  const showFull = tableColumns.showFull;
  const show = tableColumns.show;
  const showPlatforms = show("platforms");
  // Жёсткая раскладка нужна только полному набору: там колонки-названия
  // (локации, роли) иначе раздувают таблицу по самому длинному слову. В кратком
  // виде колонок мало, и auto-раскладка отдаёт остаток имени участника.
  const fixedLayout = showFull && wideTable;
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
      remaining_total: me.remaining_total,
      forecast_date: me.forecast_date,
      week_location: me.week_location,
    };
    return [...data.rows, myRow];
  }, [data, me]);

  const rows = useMemo<LeaderboardRow[]>(() => {
    let result = allRows.slice();
    // Сортировка по столбцу карты: сначала побывавшие, потом остальные. Внутри
    // групп порядок рейтинга сохраняется — sort в JS стабильная.
    const sortedLocation = sortedLightKey(sortKey);
    const sortedColumn = sortedLocation
      ? lightColumns.find(({ location }) => location.key === sortedLocation)
      : undefined;
    if (sortedColumn) {
      const wasHere = (row: LeaderboardRow) =>
        (row.row_key && sortedColumn.visits.has(row.row_key) ? 1 : 0) *
        (lightSortMissingFirst ? -1 : 1);
      result.sort((a, b) => wasHere(b) - wasHere(a));
    } else {
      result.sort((a, b) => sortValue(b, sortKey) - sortValue(a, sortKey));
    }
    // Фильтры столбцов комбинируются по И: так спрашивают «был в этом парке,
    // но не был в соседнем».
    for (const { location, visits } of lightColumns) {
      const mode = lightFilters[location.key];
      if (!mode) {
        continue;
      }
      result = result.filter((row) => {
        const wasHere = Boolean(row.row_key && visits.has(row.row_key));
        return mode === "yes" ? wasHere : !wasHere;
      });
    }
    const normalized = query.trim().toLowerCase();
    if (normalized) {
      result = result.filter((row) =>
        (row.display_name ?? "").toLowerCase().includes(normalized),
      );
    }
    return result;
  }, [allRows, sortKey, query, lightColumns, lightFilters, lightSortMissingFirst]);

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
  // Ширина colspan «нет строк»/детализации ролей: сколько колонок реально
  // нарисовано. Группы шире одной ячейки: «геo» — города+регионы, «дом» —
  // локаций+дом, «системы» — по столбцу на систему.
  const totalColumns = lbColumns.reduce((sum, column) => {
    if (!show(column.key)) {
      return sum;
    }
    if (column.key === "platforms") {
      return sum + columns.length;
    }
    if (column.key === "geo" || column.key === "home" || column.key === "forecast") {
      return sum + 2;
    }
    return sum + 1;
  }, 0);
  const visibleRows = rows.slice(0, visibleCount);
  const nextChunkEnd = Math.min(visibleCount + PAGE_STEP, rows.length);

  // Перцентиль осмысленнее абсолютного места: «опережаете 97 %» мотивирует
  // и 128-го, и 3128-го. entrants — все прошедшие порог рейтинга.
  const entrants = data?.entrants ?? 0;
  const percentileText =
    me?.included && me.rank != null && entrants > 1
      ? formatPercentile(me.rank, entrants)
      : null;

  // Тем, кто порог рейтинга ещё не прошёл, место в таблице не полагается — но
  // само место известно, и без него строка «появитесь после N» звучит как
  // «вас тут нет вовсе».
  const overallRank = me?.included === false ? me.rank_overall ?? null : null;
  const overallRankText =
    overallRank != null && overallRank > 0 && entrants > 1
      ? `Пока это ${formatInt(overallRank)}-е место из ${formatInt(entrants)}.`
      : null;

  // «Я в рейтинге, но себя в таблице не вижу»: порог рейтинга человек прошёл,
  // а в таблицу влезает только топ-1000 — при 48 тысячах участников это два
  // разных числа, и без объяснения выглядит как «меня нет в рейтинге»
  // (репорт Дмитрия 11.08.2026: 57 волонтёрств, порог 10, а себя не видно).
  // Считаем по строкам ИЗ ОТВЕТА, а не по allRows: туда своя строка уже
  // дописана искусственно и занизила бы порог попадания до собственного числа.
  const serverRows = data?.rows ?? [];
  const tableCutTotal =
    serverRows.length > 0 ? Math.min(...serverRows.map((row) => row.total)) : null;
  const belowTableCut =
    me?.included === true && tableCutTotal != null && me.total < tableCutTotal;
  const missingToTable = belowTableCut && me ? tableCutTotal - me.total + 1 : 0;

  // Как часто таблица пересчитывается по расписанию — приходит с бэкенда:
  // витрина обещает участнику срок и не должна хранить собственную копию этого
  // числа. Свежий протокол доезжает быстрее — его пересчёт будит сам синк.
  const refreshHours = data?.refresh_hours ?? 2;
  // Смена дома, до которой снапшот ещё не доехал: строки таблицы посчитаны
  // раньше, чем человек переехал, — значит километры в них от прежнего дома.
  const homeChangedAt = isHomeDistance ? (me?.home_location_changed_at ?? null) : null;
  const homeChangePending =
    homeChangedAt != null &&
    data?.built_at != null &&
    Date.parse(homeChangedAt) > Date.parse(data.built_at);

  // hint — значок «i» рядом с названием колонки: пояснение по наведению, как у
  // «Топ-локации». Свой title у значка перекрывает «Сортировать по этому
  // столбцу» с самого th, чтобы подсказки не наслаивались.
  const headerCell = (key: SortKey, label: string, className: string, hint?: string) => (
    <th
      key={key}
      className={`${className} lb-sortable${sortKey === key ? " lb-sorted" : ""}`}
      onClick={() => setSortKey(key)}
      title="Сортировать по этому столбцу"
      // Тап по заголовку сортирует — подсказку тач-режима здесь не показываем.
      data-tap-tooltip="off"
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
              {/* Домашняя локация задаёт нулевую точку всей таблицы, и её смена
                  доходит до строк только со следующим пересчётом — про задержку
                  честнее сказать заранее, а не оставлять человека гадать. */}
              {isHomeDistance && (
                <p className="lb-meta muted">
                  Таблица пересчитывается сразу после того, как в систему приезжает
                  новый протокол, и в любом случае раз в{" "}
                  {pluralizeRu(refreshHours, ["час", "часа", "часов"])}: если сменить
                  домашнюю локацию в настройках, километры в рейтинге обновятся в
                  пределах этого срока.
                </p>
              )}
            </header>

            <RatingsLoginBanner />

            {homeChangePending && homeChangedAt != null && (
              <HomeChangeNotice
                changedAt={homeChangedAt}
                builtAt={data.built_at}
                refreshHours={refreshHours}
                includedInTable={me?.included ?? false}
              />
            )}

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
                    {isHomeDistance && (
                      <div className="lb-controls-row">
                        <span className="lb-controls-label">
                          Домашняя локация{" "}
                          <InfoHint text={HOME_FILTER_HINT} />
                        </span>
                        <div className="lb-gender-tabs" role="group" aria-label="Домашняя локация">
                          <button
                            type="button"
                            aria-pressed={!hideAmbiguousHome}
                            className={`lb-gender-tab${!hideAmbiguousHome ? " lb-gender-tab-active" : ""}`}
                            onClick={() => setHideAmbiguousHome(false)}
                          >
                            Все
                          </button>
                          <button
                            type="button"
                            aria-pressed={hideAmbiguousHome}
                            className={`lb-gender-tab${hideAmbiguousHome ? " lb-gender-tab-active" : ""}`}
                            onClick={() => setHideAmbiguousHome(true)}
                          >
                            Только явный дом
                          </button>
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
                      {me.rank != null ? formatInt(me.rank) : "—"}
                      <RankDelta delta={me.rank_delta} />
                    </span>
                    <span className="lb-me-name">{me.display_name ?? "Вы"}</span>
                    <span className="lb-me-values">
                      {columns.map((code) => (
                        <span key={code} className="lb-me-value">
                          <span className="lb-me-platform">{PLATFORM_LABELS[code] ?? code}</span>
                          <CellValue cell={me.platforms[code]} unit={valueUnit} />
                        </span>
                      ))}
                      <span className="lb-me-value lb-me-total">
                        <span className="lb-me-platform">{totalLabel}</span>
                        <span className="lb-cell">
                          {formatInt(me.total)}
                          <Unit unit={valueUnit} />
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
                      {hasForecast && (
                        <>
                          <span className="lb-me-value">
                            <span className="lb-me-platform">
                              Осталось <InfoHint text={remainingHint(effectiveCountBy)} />
                            </span>
                            <GeoCount value={me.remaining_total} />
                          </span>
                          <span className="lb-me-value">
                            <span className="lb-me-platform">
                              Прогноз <InfoHint text={FORECAST_HINT} />
                            </span>
                            <ForecastDate
                              remaining={me.remaining_total}
                              date={me.forecast_date}
                            />
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
                      {isHomeDistance && me.home_location && (
                        <span className="lb-me-value">
                          <span className="lb-me-platform">
                            Дом <InfoHint text={HOME_LOCATION_HINT} />
                          </span>
                          <HomeLocationCell row={me} />
                        </span>
                      )}
                      {lastWinMeta && me.last_win_location && (
                        <span className="lb-me-value">
                          <span className="lb-me-platform">
                            {lastWinMeta.label} <InfoHint text={lastWinMeta.hint} />
                          </span>
                          <LastWinLocation row={me} />
                        </span>
                      )}
                      {hasWeekLocations && me.week_location && (
                        <span className="lb-me-value lb-me-value-wide">
                          <span className="lb-me-platform">
                            Последняя неделя{" "}
                            <InfoHint text={WEEK_LOCATIONS_HINT[metric] ?? ""} />
                          </span>
                          <WeekLocationCell item={me.week_location} />
                        </span>
                      )}
                    </span>
                    {/* Кнопка и перцентиль — одна связка: приписка встаёт справа
                        от кнопки и переносится вместе с ней, а не занимает
                        отдельную строку высотой в целый ряд. */}
                    {(myIndex >= 0 ||
                      percentileText != null ||
                      belowTableCut ||
                      shareSheet !== null) && (
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
                        {shareSheet !== null && data !== null && me.rank != null && (
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={() => {
                              const subject = ratingSubject(data, me, currentUser ?? null);
                              if (subject) {
                                shareSheet.open({ subject, entry: "rating" });
                              }
                            }}
                          >
                            📤 Поделиться
                          </button>
                        )}
                        {percentileText != null && (
                          <p className="lb-me-percentile muted">{percentileText}</p>
                        )}
                        {belowTableCut && (
                          <p className="lb-me-percentile muted">
                            В таблице — топ-{formatInt(serverRows.length)}: туда попадают от{" "}
                            {formatInt(tableCutTotal)}{" "}
                            {unitLabel(metric, tableCutTotal, effectiveCountBy)}, вам не хватает{" "}
                            {formatInt(missingToTable)}{" "}
                            {unitLabel(metric, missingToTable, effectiveCountBy)}. Ваша строка
                            дописана в конец таблицы.
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                ) : (
                  <>
                    <p className="lb-me-threshold">
                      Вы появитесь в рейтинге после достижения {formatInt(me.threshold)}{" "}
                      {unitLabel(metric, me.threshold, effectiveCountBy)} — сейчас у вас{" "}
                      {formatInt(me.total)}{valueUnit ? ` ${valueUnit}` : ""}.
                    </p>
                    {/* Место считается и до порога: «до рейтинга не дотянул» и
                        «непонятно, где я вообще» — разные вещи, и второе
                        обиднее. Знаменатель — все, у кого метрика ненулевая. */}
                    {overallRankText != null && (
                      <p className="lb-me-percentile muted">{overallRankText}</p>
                    )}
                  </>
                )}
              </section>
            )}

            {/* Карта туристов — спойлером: на основной карте таких чисел нет
                (решение Дмитрия 15.08.2026), а тут они по делу. Свёрнута по
                умолчанию — рейтинг остаётся про таблицу. */}
            {hasTouristMap && (
              <section className="lb-tourist" aria-label="Карта туристов">
                <button
                  type="button"
                  className="lb-tourist-toggle"
                  aria-expanded={mapOpen}
                  onClick={() => setMapOpen((open) => !open)}
                >
                  <span aria-hidden>{mapOpen ? "▾" : "▸"}</span>
                  <span className="lb-tourist-toggle-text">
                    Карта туристов: сколько человек из таблицы было на каждой локации
                  </span>
                  {!mapOpen && showLights && (
                    <span className="lb-tourist-toggle-chip">
                      {lightColumns.length === 1
                        ? lightColumns[0].location.name
                        : `${lightColumns.length} ${pluralFormRu(lightColumns.length, ["локация", "локации", "локаций"])} в таблице`}
                    </span>
                  )}
                </button>
                {mapOpen && (
                  <TouristMapPanel
                    state={touristMap}
                    verb={metric === "volunteer_locations" ? "волонтёрили" : "бегали"}
                    onShowTable={scrollToTable}
                  />
                )}
              </section>
            )}

            {/* Сегмент появляется, только пока краткий вид что-то прячет
                (обычно колонки систем — самая тяжёлая часть таблицы). */}
            <TableViewToggle columns={tableColumns} />
            <TableWrap
              innerRef={attachFloatingHead}
              className={`lb-table-wrap${fixedLayout ? " lb-table-wrap-wide" : ""}${
                tableColumns.hasToggle ? "" : " lb-table-wrap-flat"
              }${showLights ? " lb-table-wrap-lights" : ""}`}
              outerRef={tableColumns.measureRef}
            >
              <table
                ref={tableRef}
                className={`data-table lb-table${
                  fixedLayout ? ` lb-table-fixed ${wideTableKind}` : ""
                }${fixedLayout && noPlatformColumns ? " lb-table-no-platforms" : ""}${
                  showFull ? " lb-table-full" : ""
                }${isHomeDistance ? " lb-table-wide-values" : ""}`}
                style={showFull ? undefined : { minWidth: tableColumns.minWidth }}
              >
                <thead>
                  <tr>
                    {headerCell("rank", "Место", "lb-col-rank")}
                    <th className="lb-col-name">Участник</th>
                    {lightColumns.map(({ location }, index) => {
                      const columnKey: SortKey = `light:${location.key}`;
                      const sorted = sortKey === columnKey;
                      const filter = lightFilters[location.key];
                      return (
                        <th
                          key={location.key}
                          className={`lb-col-light${sorted ? " lb-sorted" : ""}`}
                        >
                          <span
                            className="lb-col-light-head lb-sortable"
                            role="button"
                            tabIndex={0}
                            title={
                              sorted
                                ? "Нажмите, чтобы перевернуть порядок"
                                : "Сортировать: сначала те, кто здесь был"
                            }
                            onClick={() => sortByLightColumn(columnKey)}
                            onKeyDown={(event) => {
                              if (event.key === "Enter" || event.key === " ") {
                                event.preventDefault();
                                sortByLightColumn(columnKey);
                              }
                            }}
                          >
                            <span className="lb-col-light-title">
                              Был здесь
                              {/* Пояснение вешаем на первый столбец: у пятнадцати
                                  одинаковых значков «i» смысла нет. */}
                              {index === 0 && <InfoHint text={VISIT_LIGHT_HINT} />}
                              {sorted && (
                                <span className="lb-sort-mark" aria-hidden>
                                  {lightSortMissingFirst ? "▴" : "▾"}
                                </span>
                              )}
                            </span>
                            <span className="lb-col-light-name" title={location.name}>
                              {location.name}
                            </span>
                          </span>
                          <span className="lb-col-light-actions">
                            {/* Фильтр по кругу: все → только был → только не был.
                                Комбинируется с такими же фильтрами соседних
                                столбцов — так спрашивают «был тут, но не был там». */}
                            <button
                              type="button"
                              className={`lb-col-light-filter${
                                filter ? ` lb-col-light-filter-${filter}` : ""
                              }`}
                              aria-label={`Фильтр по «${location.name}»`}
                              title={
                                filter === "yes"
                                  ? "Показаны только те, кто здесь был. Нажмите — только те, кто не был"
                                  : filter === "no"
                                    ? "Показаны только те, кто здесь не был. Нажмите — снять фильтр"
                                    : "Показать только тех, кто здесь был"
                              }
                              onClick={() => cycleLightFilter(location.key)}
                            >
                              <span aria-hidden />
                            </button>
                            <button
                              type="button"
                              className="lb-col-light-clear"
                              aria-label={`Убрать столбец «${location.name}»`}
                              title="Убрать столбец локации"
                              onClick={() => touristMap.toggle(location.key)}
                            >
                              ×
                            </button>
                          </span>
                        </th>
                      );
                    })}
                    {show("best_time") &&
                      hasWinExtras &&
                      headerCell("best_time", "Лучшее время", "lb-col-time", BEST_TIME_HINT)}
                    {/* Колонки систем не сортируются — для «посмотреть одну
                        систему» есть фильтр «Система» выше, он пересчитывает
                        место и «Всего», а не переставляет строки по столбцу. */}
                    {showPlatforms &&
                      columns.map((code) => (
                        <th key={code} className="lb-col-num">
                          {PLATFORM_LABELS[code] ?? code}
                        </th>
                      ))}
                    {headerCell(
                      "total",
                      totalLabel,
                      "lb-col-num lb-col-total",
                      isRoles ? ROLES_TOTAL_HINT : undefined,
                    )}
                    {show("geo") && hasGeoColumns && (
                      <>
                        <th className="lb-col-num lb-col-geo">
                          Городов <InfoHint text={CITIES_HINT} />
                        </th>
                        <th className="lb-col-num lb-col-geo">
                          Регионов <InfoHint text={REGIONS_HINT} />
                        </th>
                      </>
                    )}
                    {show("forecast") && hasForecast && (
                      <>
                        {headerCell(
                          "remaining",
                          "Осталось",
                          "lb-col-num lb-col-geo",
                          remainingHint(effectiveCountBy),
                        )}
                        <th className="lb-col-forecast">
                          Прогноз <InfoHint text={FORECAST_HINT} />
                        </th>
                      </>
                    )}
                    {show("home") && isHomeDistance && (
                      <>
                        <th className="lb-col-num lb-col-geo">
                          Локаций <InfoHint text={HOME_DISTANCE_LOCATIONS_HINT} />
                        </th>
                        <th className="lb-col-home">
                          Дом <InfoHint text={HOME_LOCATION_HINT} />
                        </th>
                      </>
                    )}
                    {show("top_role") && isRoles && (
                      <th className="lb-col-home">
                        Любимая роль <InfoHint text={TOP_ROLE_HINT} />
                      </th>
                    )}
                    {show("top_location") && metric === "wins" && (
                      <th className="lb-col-home">
                        Топ-локация <InfoHint text={TOP_LOCATION_HINT} />
                      </th>
                    )}
                    {show("last_win") && lastWinMeta && (
                      <th className="lb-col-last-win">
                        {lastWinMeta.label} <InfoHint text={lastWinMeta.hint} />
                      </th>
                    )}
                    {show("week") && hasWeekLocations && (
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
                        // Раз тап уже разворачивает строку — подсказки по title
                        // внутри неё в тач-режиме не всплывают.
                        data-tap-tooltip={isRoles ? "off" : undefined}
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
                            {formatInt(row.rank)}
                            <RankDelta delta={row.rank_delta} />
                          </span>
                        </td>
                        <td className="lb-col-name">
                          <ParticipantName row={row} />
                        </td>
                        {lightColumns.map(({ location, visits, loading: visitsLoading }) => (
                          <td key={location.key} className="lb-col-light">
                            {visitsLoading ? (
                              <span className="lb-light lb-light-loading" aria-label="Считаем" />
                            ) : (
                              <VisitLight
                                visit={row.row_key ? visits.get(row.row_key) : undefined}
                                covered={
                                  row.row_key != null &&
                                  touristMap.coveredRows.has(row.row_key)
                                }
                                locationName={location.name}
                                limit={touristMap.map?.limit ?? 100}
                              />
                            )}
                          </td>
                        ))}
                        {show("best_time") && hasWinExtras && (
                          <td className="lb-col-time">
                            <BestTime row={row} />
                          </td>
                        )}
                        {showPlatforms &&
                          columns.map((code) => (
                            <td key={code} className="lb-col-num">
                              <CellValue cell={row.platforms[code]} unit={valueUnit} />
                            </td>
                          ))}
                        <td className="lb-col-num lb-col-total">
                          <span className="lb-cell lb-total">
                            {formatInt(row.total)}
                            <Unit unit={valueUnit} />
                            <DeltaSlot delta={row.total_delta} />
                          </span>
                        </td>
                        {show("geo") && hasGeoColumns && (
                          <>
                            <td className="lb-col-num lb-col-geo">
                              <GeoCount value={row.cities_total} />
                            </td>
                            <td className="lb-col-num lb-col-geo">
                              <GeoCount value={row.regions_total} />
                            </td>
                          </>
                        )}
                        {show("forecast") && hasForecast && (
                          <>
                            <td className="lb-col-num lb-col-geo">
                              <GeoCount value={row.remaining_total} />
                            </td>
                            <td className="lb-col-forecast">
                              <ForecastDate
                                remaining={row.remaining_total}
                                date={row.forecast_date}
                              />
                            </td>
                          </>
                        )}
                        {show("home") && isHomeDistance && (
                          <>
                            <td className="lb-col-num lb-col-geo">
                              <GeoCount value={row.locations_total} />
                            </td>
                            <td className="lb-col-home">
                              <HomeLocationCell row={row} />
                            </td>
                          </>
                        )}
                        {show("top_role") && isRoles && (
                          <td className="lb-col-home">
                            <TopRole row={row} />
                          </td>
                        )}
                        {show("top_location") && metric === "wins" && (
                          <td className="lb-col-home">
                            <TopWinLocation row={row} />
                          </td>
                        )}
                        {show("last_win") && lastWinMeta && (
                          <td className="lb-col-last-win">
                            <LastWinLocation row={row} />
                          </td>
                        )}
                        {show("week") && hasWeekLocations && (
                          <td className="lb-col-last-win">
                            <WeekLocationCell item={row.week_location} />
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
                value={`${formatInt(me.total)}${valueUnit ? ` ${valueUnit}` : ""}`}
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
