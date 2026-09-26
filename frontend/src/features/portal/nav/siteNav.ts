/**
 * Единое дерево навигации сайта (вариант В, решение Дмитрия 23.09.2026).
 *
 * До этого у сайта было семь меню, собранных вручную: шапка, сайдбар, бургер,
 * две нижние панели телефона, две разные шторки «Ещё» и подвал. Они
 * разъехались — одна и та же страница в одном меню была, в другом нет, а
 * «Погоды» и 14 рейтингов не было ни в одном. Люди писали в личку «как найти».
 *
 * Теперь всё рисуется отсюда:
 * - рельс разделов и колонка подразделов на компьютере (SiteSidebar);
 * - ссылки разделов в шапке на страницах без рельса и колонки подвала;
 * - нижняя панель, липкая полоса страниц раздела и шторка «Меню» на телефоне;
 * - поиск по страницам сайта — через keywords (синонимы).
 *
 * Один раздел — одно имя везде (решение Дмитрия 26.09.2026): «Кабинет»,
 * «Оргкабинет», «Итоги», «Локации», «Рейтинги». Раньше кабинет звался «Моё»,
 * «Мой кабинет», «Личный кабинет» и «Участнику», и человек не узнавал в меню
 * слово, которое только что видел на странице.
 *
 * Добавляете страницу — добавьте её сюда, и она появится везде сразу; новый
 * верхний адрес раздела — ещё и в SECTION_PATH_PREFIXES. Синонимы пишите
 * словами, которыми человек назвал бы страницу: «погода», «кто быстрее всех»,
 * «привязать профиль» — без служебных «где», «как», «мой».
 */
import type { ReactNode } from "react";
import type { User } from "../../../lib/api";
import { getRecentLocations } from "../../../lib/recentLocations";
import * as I from "./navIcons";
import { organizerEntryPlace, organizerHasManyPlaces, rememberedOrganizerRole } from "./organizerMemory";
import {
  cabinetTabHref,
  type CabinetTabSegmentKey,
  PORTAL_ABOUT_HREF,
  PORTAL_BLOG_HREF,
  PORTAL_CABINET_SETTINGS_HREF,
  PORTAL_CABINET_SHARE_HREF,
  PORTAL_HOME_HREF,
  PORTAL_LOGIN_HREF,
  PORTAL_UPDATES_HREF,
} from "../../../lib/portalRoutes";

export type NavSectionKey = "me" | "organizer" | "results" | "locations" | "ratings" | "project" | "account";

export type NavLink = {
  key: string;
  label: string;
  href: string;
  /** Подпись вкладки на телефоне, если полная не влезает. */
  chipLabel?: string;
  /**
   * Подпись в колонке на компьютере, если полная переносится на две строки
   * (колонка узкая, 224px). Смысл тот же, группа над пунктом его дополняет;
   * полное имя — во всплывающей подсказке и в заголовке страницы.
   */
  colLabel?: string;
  /** Иконка страницы: колонка, «Меню», переключатель, поиск. */
  icon?: ReactNode;
  /** Синонимы для поиска по сайту (в нижнем регистре). */
  keywords?: readonly string[];
  /** Текущая ли страница, когда одного совпадения адреса мало. */
  matches?: (pathname: string) => boolean;
  /** Служебный пункт (админка) — подсвечивается янтарным. */
  tone?: "admin";
};

export type NavGroup = {
  key: string;
  /** Подзаголовок группы в колонке; у первой группы раздела обычно пустой. */
  title?: string;
  /** Группа открытой сущности (локация, кабинет организатора локации). */
  context?: boolean;
  items: NavLink[];
};

export type NavSection = {
  key: NavSectionKey;
  label: string;
  /**
   * Подпись под иконкой в рельсе и на нижней панели. С 26.09.2026 совпадает
   * с label — разные слова для одного раздела путали; отличается только у
   * гостя («Войти» вместо «Кабинета»).
   */
  shortLabel: string;
  href: string;
  groups: NavGroup[];
  keywords?: readonly string[];
  /** Адреса раздела: по ним раздел узнаётся, если страница его не назвала. */
  pathPrefixes: readonly string[];
  /**
   * Есть ли у раздела иконка в рельсе на компьютере. «О проекте» и аккаунт —
   * не места, куда ходят за статистикой (правка Дмитрия 25.09.2026): первое
   * живёт в шапке и подвале, второе — в меню под именем в шапке. В дереве
   * они остаются ради «Меню» на телефоне, поиска и колонки на своих страницах.
   * По умолчанию раздел в рельсе есть.
   */
  inRail?: boolean;
};

export type NavPlace = { slug: string; name: string };

export type NavContext = {
  /** User — вошёл, null — аноним, undefined — сессия ещё проверяется. */
  user: User | null | undefined;
  /** Открытая локация (публичные страницы /locations/{slug}/…). */
  location?: NavPlace | null;
  /** Открытый кабинет организатора (/organizer/{slug}/…). */
  organizerLocation?: NavPlace | null;
  /**
   * Человек сейчас в разделе организатора. Вне раздела «Оргкабинет» ведёт в
   * последнюю открытую (или единственную) локацию, а внутри, на «Моих
   * локациях», — показывает сам список.
   */
  inOrganizer?: boolean;
};

// Ключи вкладок кабинета; "share" и "settings" живут на своих адресах.
export type CabinetTabKey = CabinetTabSegmentKey | "share" | "settings";

function normalizePath(path: string): string {
  const trimmed = path.replace(/\/+$/, "");
  try {
    return decodeURIComponent(trimmed || "/");
  } catch {
    return trimmed || "/";
  }
}

export function isLinkCurrent(link: NavLink, pathname: string): boolean {
  if (link.matches) {
    return link.matches(pathname);
  }
  return normalizePath(link.href) === normalizePath(pathname);
}

// ---------- адреса разделов ----------

/**
 * Верхние адреса разделов. Раздел страницы узнаётся по ним, если страница
 * сама его не передала (главная, блог, нижняя панель в шапке). Кабинет живёт
 * на /users/{свой хендл} — это решает navState, адрес тут не перечислить.
 */
export const SECTION_PATH_PREFIXES: Record<NavSectionKey, readonly string[]> = {
  me: [PORTAL_CABINET_SHARE_HREF, "/new/"],
  organizer: ["/organizer"],
  results: ["/results", "/protocol"],
  locations: ["/locations"],
  ratings: ["/ratings"],
  project: [PORTAL_ABOUT_HREF, PORTAL_BLOG_HREF, PORTAL_UPDATES_HREF, "/backlog"],
  account: [PORTAL_CABINET_SETTINGS_HREF, "/admin"],
};

function pathHasPrefix(pathname: string, prefix: string): boolean {
  if (prefix.endsWith("/")) return pathname.startsWith(prefix);
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

/**
 * Раздел по адресу страницы — для страниц, которые не передали `active`
 * (главная, блог, «О проекте») и для нижней панели, которая живёт в шапке.
 */
export function sectionKeyFromPath(pathname: string): NavSectionKey | null {
  for (const [key, prefixes] of Object.entries(SECTION_PATH_PREFIXES) as [NavSectionKey, readonly string[]][]) {
    if (prefixes.some((prefix) => pathHasPrefix(pathname, prefix))) return key;
  }
  return null;
}

// ---------- Кабинет ----------

type CabinetDef = { key: CabinetTabKey; label: string; icon: ReactNode; keywords: readonly string[] };

// Порядок — по спросу с прода (30 дней до 23.09.2026): обзор, достижения,
// пробежки, карта, история, волонтёрство, встречи. В шторке переключателя на
// телефоне самые открываемые оказываются под пальцем первыми.
export const CABINET_TABS: readonly CabinetDef[] = [
  {
    key: "dashboard",
    label: "Обзор",
    icon: I.CABINET_ICONS.dashboard,
    keywords: ["кабинет", "личный кабинет", "профиль", "моя статистика", "дашборд", "главная кабинета"],
  },
  {
    key: "achievements",
    label: "Достижения",
    icon: I.CABINET_ICONS.achievements,
    keywords: ["значки", "награды", "челленджи", "клубы", "юбилей", "ачивки", "майка", "футболка"],
  },
  {
    key: "runs",
    label: "Пробежки",
    icon: I.CABINET_ICONS.runs,
    keywords: ["мои пробежки", "мои результаты", "мое время", "личный рекорд", "рекорд", "pb", "темп", "забеги"],
  },
  {
    key: "map",
    label: "Карта",
    icon: I.CABINET_ICONS.map,
    keywords: ["моя карта", "где бегал", "где я бегал", "туризм", "мои локации", "география"],
  },
  {
    key: "history",
    label: "Моя история",
    icon: I.CABINET_ICONS.history,
    // Настройка вех живёт шестерёнкой на этой вкладке (перенесена из
    // «Настроек» 23.09.2026) — поиск «вехи» должен вести сюда.
    keywords: ["история", "хронология", "первый забег", "первая пробежка", "лента", "по годам", "вехи", "настройка вех"],
  },
  {
    key: "volunteering",
    label: "Волонтёрство",
    icon: I.CABINET_ICONS.volunteering,
    keywords: ["мои волонтерства", "волонтер", "роли", "помощь", "организация забега"],
  },
  {
    key: "meetings",
    label: "Встречи",
    icon: I.CABINET_ICONS.meetings,
    keywords: ["с кем бегаю", "с кем бегал", "знакомые", "друзья", "попутчики", "соседи по забегу"],
  },
];

// «Поделиться» — последним пунктом кабинета: это действие над своей
// статистикой, а не отдельное место, но искать его люди будут именно здесь.
const CABINET_SHARE: CabinetDef = {
  key: "share",
  label: "Поделиться",
  icon: I.CABINET_ICONS.share,
  keywords: ["сторис", "картинка", "постер", "поделиться результатом"],
};

/** Все ключи страниц кабинета — из того же списка, что и сами вкладки. */
export const CABINET_TAB_KEYS: readonly CabinetTabKey[] = [
  ...CABINET_TABS.map((tab) => tab.key),
  CABINET_SHARE.key,
  "settings",
];

// Настройки — не страница кабинета, а настройки всего сайта и аккаунта
// (правка Дмитрия 25.09.2026): живут в разделе «Аккаунт».
const SETTINGS_DEF: CabinetDef = {
  key: "settings",
  label: "Настройки",
  icon: I.SETTINGS_ICON,
  keywords: [
    "имя",
    "сменить имя",
    "аватар",
    "фото профиля",
    "привязать профиль",
    "привязка",
    "штрихкод",
    "почта",
    "email",
    "телеграм",
    "вк",
    "приватность",
    "закрыть профиль",
    "домашняя локация",
    "удалить аккаунт",
    "уведомления",
    "рассылка",
    "способы входа",
  ],
};

export function cabinetHref(user: User | null | undefined, key: CabinetTabKey): string {
  if (key === "share") return PORTAL_CABINET_SHARE_HREF;
  if (key === "settings") return PORTAL_CABINET_SETTINGS_HREF;
  return cabinetTabHref(user ?? null, key);
}

export const CABINET_LABEL = "Кабинет";

function meSection(ctx: NavContext): NavSection {
  const anon = ctx.user === null;
  const link = (def: CabinetDef): NavLink => ({
    key: def.key,
    label: def.label,
    icon: def.icon,
    href: cabinetHref(ctx.user, def.key),
    keywords: def.keywords,
  });
  return {
    key: "me",
    label: CABINET_LABEL,
    shortLabel: anon ? "Войти" : CABINET_LABEL,
    href: anon ? PORTAL_LOGIN_HREF : cabinetHref(ctx.user, "dashboard"),
    keywords: ["кабинет", "мой кабинет", "личный кабинет", "войти", "вход", "логин", "регистрация"],
    pathPrefixes: SECTION_PATH_PREFIXES.me,
    // Анониму пунктов нет: колонка зовёт войти (см. SiteSidebar).
    groups: anon
      ? []
      : [
          { key: "tabs", items: [...CABINET_TABS, CABINET_SHARE].map(link) },
        ],
  };
}

// ---------- Оргкабинет ----------

type ToolDef = {
  key: string;
  label: string;
  chipLabel?: string;
  colLabel?: string;
  icon: ReactNode;
  path: string;
  keywords: readonly string[];
};

// Группы — по тому, зачем организатор приходит: собрать отчёт о субботе,
// посмотреть на людей, спланировать команду. Названия — как на карточках хаба.
const ORGANIZER_TOOL_GROUPS: readonly { key: string; title: string; tools: readonly ToolDef[] }[] = [
  {
    key: "saturday",
    title: "Субботний отчёт",
    tools: [
      { key: "hub", label: "Обзор кабинета", icon: I.GRID_ICON, chipLabel: "Обзор", path: "", keywords: ["светофор", "здоровье локации"] },
      { key: "report", label: "Свод по пробежке", icon: I.REPORT_ICON, chipLabel: "Свод", path: "report", keywords: ["отчет", "свод", "итоги субботы"] },
      { key: "post", label: "Пост-отчёт", icon: I.POST_ICON, chipLabel: "Пост", path: "post", keywords: ["пост", "текст для канала", "анонс", "постер"] },
      { key: "protocols", label: "Протоколы", icon: I.PROTOCOL_SPEED_ICON, path: "protocols", keywords: ["скорость протокола", "выгрузка протокола", "задержка"] },
    ],
  },
  {
    key: "people",
    title: "Люди",
    tools: [
      { key: "milestones", label: "Календарь юбилеев", icon: I.MILESTONES_ICON, chipLabel: "Юбилеи", path: "milestones", keywords: ["юбилеи", "клуб 50", "клуб 100", "поздравить"] },
      { key: "newcomers", label: "Удержание новичков", icon: I.NEWCOMERS_ICON, chipLabel: "Новички", path: "newcomers", keywords: ["новички", "дебютанты", "первый старт", "вернулись"] },
      { key: "absence", label: "Долгая пауза", icon: I.PAUSE_ICON, chipLabel: "Пауза", path: "absence", keywords: ["пропали", "давно не было", "пауза", "вернуть участников"] },
      { key: "audience", label: "Портрет участника", icon: I.AUDIENCE_ICON, chipLabel: "Портрет", path: "audience", keywords: ["аудитория", "возраст", "пол", "клубы участников"] },
    ],
  },
  {
    key: "team",
    title: "Команда",
    tools: [
      { key: "volunteers", label: "Волонтёрская скамейка", colLabel: "Скамейка волонтёров", icon: I.BENCH_ICON, chipLabel: "Скамейка", path: "volunteers", keywords: ["скамейка", "кого позвать", "резерв волонтеров"] },
      { key: "team", label: "Команда и нагрузка", icon: I.TEAM_ICON, chipLabel: "Команда", path: "team", keywords: ["нагрузка", "ротация", "организаторы дня"] },
      { key: "attendance", label: "Посещаемость", icon: I.ATTENDANCE_ICON, path: "attendance", keywords: ["явка", "посещаемость", "журнал посещаемости"] },
      { key: "benchmark", label: "Мы и соседи", icon: I.BENCHMARK_ICON, chipLabel: "Соседи", path: "benchmark", keywords: ["сравнение", "соседние локации", "бенчмарк"] },
    ],
  },
];

export const ORGANIZER_INDEX_HREF = "/organizer";
export const ORGANIZER_LABEL = "Оргкабинет";

/**
 * Показывать ли «Оргкабинет». Пока сессия проверяется (user === undefined),
 * верим тому, что помнит браузер с прошлого раза: иначе в новой вкладке рельс
 * и нижняя панель через секунду перестраивались (a11y-6).
 */
export function canSeeOrganizer(user: User | null | undefined): boolean {
  if (user === undefined) return rememberedOrganizerRole();
  return user != null && (user.is_organizer || user.is_admin);
}

function organizerSection(ctx: NavContext): NavSection {
  // Внутри раздела — открытая локация (или её нет: страница «Мои локации»).
  // Вне раздела — куда ведёт «Оргкабинет»: последняя открытая или
  // единственная своя локация (см. organizerMemory). Её инструменты тоже в
  // дереве: поиск «юбилеи» находит их с любой страницы.
  const place = ctx.inOrganizer ? (ctx.organizerLocation ?? null) : organizerEntryPlace(ctx.user);
  const indexGroup: NavGroup = {
    key: "index",
    items: [
      {
        key: "index",
        label: "Мои локации",
        icon: I.CATALOG_ICON,
        href: ORGANIZER_INDEX_HREF,
        keywords: ["выбрать локацию", "другая локация", "список локаций организатора"],
      },
    ],
  };
  if (!place) {
    // Место неизвестно: страница самого списка или последняя локация ещё не
    // запомнена — в разделе один пункт, список.
    return organizerSectionShell([indexGroup], ORGANIZER_INDEX_HREF);
  }
  const groups: NavGroup[] = ORGANIZER_TOOL_GROUPS.map((group) => ({
    key: group.key,
    title: group.title,
    context: true,
    items: group.tools.map((tool) => ({
      key: tool.key,
      label: tool.label,
      chipLabel: tool.chipLabel,
      colLabel: tool.colLabel,
      icon: tool.icon,
      href: `/organizer/${place.slug}${tool.path ? `/${tool.path}` : ""}`,
      keywords: tool.keywords,
    })),
  }));
  // «Мои локации» рядом с инструментами — только когда есть из чего выбирать:
  // больше одной своей локации или админ. При одной список сразу вернул бы в
  // неё же (a11y-10), а у организатора нескольких без этого пункта список
  // не находил поиск и не показывало «Меню» (V9). В колонке он последний: там
  // над инструментами есть ещё и переключатель локаций.
  if (organizerHasManyPlaces(ctx.user)) {
    groups.push(indexGroup);
  }
  return organizerSectionShell(groups, `/organizer/${place.slug}`);
}

function organizerSectionShell(groups: NavGroup[], href: string): NavSection {
  return {
    key: "organizer",
    label: ORGANIZER_LABEL,
    shortLabel: ORGANIZER_LABEL,
    href,
    keywords: ["организатор", "оргкоманда", "кабинет организатора", "директор забега"],
    pathPrefixes: SECTION_PATH_PREFIXES.organizer,
    groups,
  };
}

// ---------- Итоги ----------

export const RESULTS_LABEL = "Итоги";

function resultsSection(): NavSection {
  return {
    key: "results",
    label: RESULTS_LABEL,
    shortLabel: RESULTS_LABEL,
    href: "/results",
    keywords: ["итоги", "результаты"],
    pathPrefixes: SECTION_PATH_PREFIXES.results,
    groups: [
      {
        key: "results",
        items: [
          {
            key: "last-results",
            label: "Последние пробежки",
            icon: I.RESULTS_ICON,
            chipLabel: "Последние",
            href: "/results",
            keywords: [
              "результаты",
              "итоги недели",
              "прошлая суббота",
              "эта суббота",
              "последний забег",
              "свежие результаты",
              "кто пробежал",
            ],
          },
          {
            key: "unified-protocol",
            label: "Единый протокол",
            icon: I.UNIFIED_PROTOCOL_ICON,
            href: "/protocol",
            matches: (pathname) => pathname === "/protocol" || pathname.startsWith("/protocol/"),
            keywords: ["вся страна", "все финишеры", "общий протокол", "протокол недели", "все локации сразу"],
          },
        ],
      },
    ],
  };
}

// ---------- Локации ----------

/**
 * Страницы одной локации. Нужны и колонке с полосой страниц (когда локация открыта), и
 * поиску: «погода сокол» складывает синоним страницы с найденной локацией.
 */
export const LOCATION_PAGES: readonly {
  key: string;
  label: string;
  chipLabel?: string;
  icon: ReactNode;
  suffix: string;
  keywords: readonly string[];
  matches?: (pathname: string, base: string) => boolean;
}[] = [
  { key: "location", label: "Обзор", icon: I.PLACE_ICON, suffix: "", keywords: ["трасса", "как добраться", "описание", "адрес", "старт"] },
  {
    key: "events",
    label: "Журнал протоколов",
    icon: I.PROTOCOL_ICON,
    chipLabel: "Протоколы",
    suffix: "/events",
    keywords: ["протоколы", "протокол", "история стартов", "все старты", "архив", "журнал"],
    matches: (pathname, base) => pathname === `${base}/events` || pathname.startsWith(`${base}/protocol/`),
  },
  {
    key: "participants",
    label: "Постоянный состав",
    icon: I.REGULARS_ICON,
    chipLabel: "Состав",
    suffix: "/participants",
    keywords: ["постоянные участники", "завсегдатаи", "регулярные", "состав", "кто бегает", "сообщество"],
  },
  {
    key: "tops",
    label: "Топы бегунов",
    icon: I.PODIUM_ICON,
    chipLabel: "Топы",
    suffix: "/tops",
    keywords: ["топ", "лучшие", "самые быстрые", "рекорд трассы", "лидеры", "победители"],
  },
  {
    key: "weather",
    label: "Погода",
    icon: I.WEATHER_ICON,
    suffix: "/weather",
    keywords: ["погода", "температура", "дождь", "ветер", "снег", "мороз", "жара", "прогноз", "градусы"],
  },
];

export function locationPageLinks(place: NavPlace): NavLink[] {
  const base = `/locations/${place.slug}`;
  return LOCATION_PAGES.map((page) => ({
    key: page.key,
    label: page.label,
    chipLabel: page.chipLabel,
    icon: page.icon,
    href: `${base}${page.suffix}`,
    keywords: page.keywords,
    matches: page.matches ? (pathname: string) => page.matches!(pathname, base) : undefined,
  }));
}

// Сколько недавних локаций показывать в колонке и «Меню»: больше трёх —
// это уже снова каталог.
const RECENT_LIMIT = 3;

/**
 * Своя локация и недавние (идея Г, решение Дмитрия 26.09.2026). Колонка
 * «Локации» состояла из одного пункта, а чаще всего на сайт приходят именно
 * на страницу своего парка: теперь она и пара недавних — в один клик из
 * колонки, «Меню» и поиска.
 *
 * Домашнюю локацию отдаёт сервер (User.home_location); пока поля нет, пункта
 * просто нет. Недавние — из localStorage этого браузера (lib/recentLocations).
 */
function personalLocationLinks(ctx: NavContext): { home: NavLink | null; recent: NavLink[] } {
  const homePlace = ctx.user?.home_location ?? null;
  const home: NavLink | null = homePlace
    ? {
        key: `home:${homePlace.slug}`,
        label: `Моя: ${homePlace.name}`,
        chipLabel: homePlace.name,
        icon: I.HOME_ICON,
        href: `/locations/${homePlace.slug}`,
        keywords: ["моя локация", "домашняя локация", "мой парк", homePlace.name.toLowerCase()],
        // Ярлык, а не страница: на обзоре своей локации текущим отмечен
        // «Обзор» в её группе, двух подсвеченных пунктов быть не должно (V10).
        matches: () => false,
      }
    : null;
  const skip = new Set([homePlace?.slug, ctx.location?.slug].filter(Boolean));
  const recent = getRecentLocations()
    .filter((place) => !skip.has(place.slug))
    .slice(0, RECENT_LIMIT)
    .map((place) => ({
      key: `recent:${place.slug}`,
      label: place.name,
      icon: I.RECENT_ICON,
      href: `/locations/${place.slug}`,
      keywords: ["недавние", "недавно открытые", place.name.toLowerCase()],
    }));
  return { home, recent };
}

function locationsSection(ctx: NavContext): NavSection {
  const { home, recent } = personalLocationLinks(ctx);
  const catalog: NavLink = {
    key: "catalog",
    label: "Все локации",
    icon: I.CATALOG_ICON,
    href: "/locations",
    keywords: ["каталог", "список локаций", "парки", "где бегать", "карта локаций", "ближайшая локация"],
  };
  const groups: NavGroup[] = [{ key: "catalog", items: home ? [catalog, home] : [catalog] }];
  if (recent.length > 0) {
    groups.push({ key: "recent", title: "Недавние", items: recent });
  }
  if (ctx.location) {
    groups.push({ key: "place", title: ctx.location.name, context: true, items: locationPageLinks(ctx.location) });
  }
  return {
    key: "locations",
    label: "Локации",
    shortLabel: "Локации",
    href: "/locations",
    keywords: ["локации", "парки", "места"],
    pathPrefixes: SECTION_PATH_PREFIXES.locations,
    groups,
  };
}

// ---------- Рейтинги ----------

// Те же группы и названия, что на хабе /ratings: человек видит один и тот же
// рейтинг под одним именем и в меню, и на карточке.
// Экспорт: хаб рейтингов и заголовки страниц берут названия отсюда, чтобы
// один рейтинг не назывался в меню и на карточке по-разному. colLabel —
// только для узкой колонки: группа («Бегуны», «Волонтёры») уже говорит, о ком
// рейтинг, и «Пробежки» под «Бегунами» читаются как «Количество пробежек».
export const RATING_GROUPS: readonly { key: string; title: string; items: readonly NavLink[] }[] = [
  {
    key: "runners",
    title: "Бегуны",
    items: [
      { key: "runs", label: "Количество пробежек", colLabel: "Пробежки", icon: I.RUNS_RATING_ICON, chipLabel: "Пробежки", href: "/ratings/runs", keywords: ["больше всех пробежек", "самые активные"] },
      { key: "wins", label: "Первые места", icon: I.MEDAL_ICON, href: "/ratings/wins", keywords: ["победы", "победители", "первое место", "абсолют"] },
      { key: "fastest", label: "Самые быстрые", icon: I.BOLT_ICON, chipLabel: "Быстрые", href: "/ratings/fastest", keywords: ["быстрее всех", "скорость", "лучшее время", "рекорды времени"] },
    ],
  },
  {
    key: "volunteers",
    title: "Волонтёры",
    items: [
      { key: "volunteering", label: "Количество волонтёрств", colLabel: "Волонтёрства", icon: I.VOLUNTEER_ICON, chipLabel: "Волонтёрства", href: "/ratings/volunteering", keywords: ["больше всех волонтерств", "волонтеры"] },
      { key: "volunteer-locations", label: "Волонтёрство на разных локациях", colLabel: "На разных локациях", icon: I.VOLUNTEER_PLACES_ICON, chipLabel: "Локации", href: "/ratings/volunteer-locations", keywords: ["волонтерский туризм"] },
      { key: "volunteer-roles", label: "Разнообразие ролей", icon: I.ROLES_ICON, chipLabel: "Роли", href: "/ratings/volunteer-roles", keywords: ["мультиволонтер", "все роли"] },
    ],
  },
  {
    key: "tourists",
    title: "Туристы",
    items: [
      { key: "locations", label: "Уникальные локации", icon: I.GLOBE_ICON, chipLabel: "Локации", href: "/ratings/locations", keywords: ["туризм", "паркран-туристы", "больше всех локаций", "туристы"] },
      { key: "openings", label: "Открытия локаций", icon: I.FLAG_ICON, chipLabel: "Открытия", href: "/ratings/openings", keywords: ["первопроходцы", "первый старт локации", "открытие"] },
      { key: "win-locations", label: "Локации с первым местом", colLabel: "С первым местом", icon: I.CROWN_ICON, chipLabel: "С победой", href: "/ratings/win-locations", keywords: ["победы на разных локациях"] },
      { key: "home-distance", label: "Дальность от дома", icon: I.DISTANCE_ICON, chipLabel: "Дальность", href: "/ratings/home-distance", keywords: ["далеко от дома", "километры", "путешествия"] },
    ],
  },
  {
    key: "places",
    title: "Локации",
    items: [
      { key: "location-records", label: "Рекорды локаций", icon: I.RECORD_ICON, chipLabel: "Рекорды", href: "/ratings/location-records", keywords: ["рекорд трассы", "рекорды", "лучшее время на локации"] },
      // «Регионы» как заголовок страницы и карточки хаба — слишком коротко:
      // непонятно, что считают. Полное имя там, короткое — в колонке и полосе,
      // где над ним и так стоит группа «Локации».
      { key: "regions", label: "Локации по регионам", colLabel: "Регионы", chipLabel: "Регионы", icon: I.REGIONS_ICON, href: "/ratings/regions", keywords: ["регионы", "области", "города", "страны", "регионы россии"] },
    ],
  },
];

export const RATINGS_HUB_HREF = "/ratings";

function ratingsSection(ctx: NavContext): NavSection {
  const groups: NavGroup[] = RATING_GROUPS.map((group) => ({ key: group.key, title: group.title, items: [...group.items] }));
  // Трассы по трекам участников — фича пока закрыта, как и карточка на хабе:
  // видит только админ.
  if (ctx.user?.is_admin) {
    groups
      .find((group) => group.key === "places")
      ?.items.push({
        key: "courses",
        label: "Трассы локаций",
        chipLabel: "Трассы",
        icon: I.ELEVATION_ICON,
        href: "/ratings/courses",
        keywords: ["трасса", "перепад высот", "набор высоты", "горки", "профиль трассы", "треки"],
      });
  }
  return {
    key: "ratings",
    label: "Рейтинги",
    shortLabel: "Рейтинги",
    href: RATINGS_HUB_HREF,
    keywords: ["рейтинг", "лидерборд", "таблица лидеров", "топ", "кто первый"],
    pathPrefixes: SECTION_PATH_PREFIXES.ratings,
    groups: [
      { key: "hub", items: [{ key: "hub", label: "Все рейтинги", chipLabel: "Все", icon: I.GRID_ICON, href: RATINGS_HUB_HREF }] },
      ...groups,
    ],
  };
}

// ---------- О проекте ----------

export const PROJECT_LABEL = "О проекте";

function projectSection(): NavSection {
  const items: NavLink[] = [
    {
      key: "about",
      label: "О проекте",
      icon: I.INFO_ICON,
      href: PORTAL_ABOUT_HREF,
      matches: (pathname) => pathname === PORTAL_ABOUT_HREF,
      keywords: ["о сайте", "контакты", "автор", "приватность", "данные", "политика", "связаться", "поддержка"],
    },
    {
      key: "blog",
      label: "Блог",
      icon: I.BLOG_ICON,
      href: PORTAL_BLOG_HREF,
      matches: (pathname) => pathname === PORTAL_BLOG_HREF || pathname.startsWith(`${PORTAL_BLOG_HREF}/`),
      keywords: ["новости", "статьи", "посты", "телеграм канал", "канал"],
    },
    {
      key: "updates",
      label: "Обновления",
      icon: I.SPARKLES_ICON,
      href: PORTAL_UPDATES_HREF,
      keywords: ["что нового", "релизы", "версии", "изменения на сайте"],
    },
    {
      key: "backlog",
      label: "Бэклог идей",
      icon: I.IDEA_ICON,
      chipLabel: "Бэклог",
      href: "/backlog",
      keywords: ["идеи", "предложить идею", "предложение", "пожелание", "баг", "ошибка", "сообщить об ошибке"],
    },
  ];
  return {
    key: "project",
    label: PROJECT_LABEL,
    shortLabel: PROJECT_LABEL,
    href: PORTAL_ABOUT_HREF,
    pathPrefixes: SECTION_PATH_PREFIXES.project,
    groups: [{ key: "project", items }],
    inRail: false,
  };
}

// ---------- Аккаунт ----------

/**
 * Настройки и админка — то, что касается аккаунта и сайта целиком. На
 * компьютере открываются из меню под именем в шапке, на телефоне — внизу
 * «Меню»; своей иконки в рельсе у раздела нет.
 */
function accountSection(ctx: NavContext): NavSection {
  const items: NavLink[] =
    ctx.user === null
      ? []
      : [
          {
            key: SETTINGS_DEF.key,
            label: SETTINGS_DEF.label,
            icon: SETTINGS_DEF.icon,
            href: cabinetHref(ctx.user, SETTINGS_DEF.key),
            keywords: SETTINGS_DEF.keywords,
          },
        ];
  if (ctx.user?.is_admin) {
    items.push({
      key: "admin",
      label: "Админка",
      icon: I.SHIELD_ICON,
      href: "/admin/users",
      matches: (pathname) => pathname.startsWith("/admin"),
      tone: "admin",
      keywords: ["администрирование", "пользователи", "очередь"],
    });
  }
  return {
    key: "account",
    label: "Аккаунт",
    shortLabel: "Аккаунт",
    href: PORTAL_CABINET_SETTINGS_HREF,
    pathPrefixes: SECTION_PATH_PREFIXES.account,
    groups: items.length > 0 ? [{ key: "account", items }] : [],
    inRail: false,
  };
}

// ---------- сборка ----------

export function buildSiteNav(ctx: NavContext): NavSection[] {
  const sections: NavSection[] = [meSection(ctx)];
  if (canSeeOrganizer(ctx.user)) {
    sections.push(organizerSection(ctx));
  }
  sections.push(resultsSection(), locationsSection(ctx), ratingsSection(ctx), projectSection(), accountSection(ctx));
  return sections;
}

/**
 * Страницы, которых нет ни в одном меню, но которые должен находить поиск по
 * сайту. Главная — не раздел (на неё ведёт логотип), но «главная» — частый
 * запрос, и без этой строки он уводил в обзор кабинета.
 */
export const EXTRA_SEARCH_LINKS: readonly NavLink[] = [
  {
    key: "home",
    label: "Главная",
    icon: I.HOME_ICON,
    href: PORTAL_HOME_HREF,
    matches: (pathname) => pathname === PORTAL_HOME_HREF,
    keywords: ["главная", "главная страница", "начало", "стартовая", "на главную"],
  },
];

/** Все ссылки дерева одним списком — для поиска по страницам. */
export function flattenNav(sections: NavSection[]): { section: NavSection; group: NavGroup; link: NavLink }[] {
  const out: { section: NavSection; group: NavGroup; link: NavLink }[] = [];
  for (const section of sections) {
    for (const group of section.groups) {
      for (const link of group.items) {
        out.push({ section, group, link });
      }
    }
  }
  return out;
}
