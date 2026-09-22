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
 * - нижняя панель, чипы под заголовком и шторка «Меню» на телефоне;
 * - поиск по страницам сайта — через keywords (синонимы).
 *
 * Добавляете страницу — добавьте её сюда, и она появится везде сразу.
 * Синонимы пишите так, как человек спросил бы в личке: «где погода»,
 * «кто быстрее всех», «как привязать профиль».
 */
import type { User } from "../../../lib/api";
import {
  cabinetTabHref,
  type CabinetTabSegmentKey,
  PORTAL_ABOUT_HREF,
  PORTAL_BLOG_HREF,
  PORTAL_CABINET_SETTINGS_HREF,
  PORTAL_CABINET_SHARE_HREF,
  PORTAL_LOGIN_HREF,
  PORTAL_UPDATES_HREF,
} from "../../../lib/portalRoutes";

export type NavSectionKey = "me" | "organizer" | "results" | "locations" | "ratings" | "project";

export type NavLink = {
  key: string;
  label: string;
  href: string;
  /** Подпись чипа на телефоне, если полная не влезает. */
  chipLabel?: string;
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
  /** Подпись под иконкой в рельсе и на нижней панели. */
  shortLabel: string;
  href: string;
  groups: NavGroup[];
  keywords?: readonly string[];
};

export type NavPlace = { slug: string; name: string };

export type NavContext = {
  /** User — вошёл, null — аноним, undefined — сессия ещё проверяется. */
  user: User | null | undefined;
  /** Открытая локация (публичные страницы /locations/{slug}/…). */
  location?: NavPlace | null;
  /** Открытый кабинет организатора (/organizer/{slug}/…). */
  organizerLocation?: NavPlace | null;
  /** Превью кабинета на демо-данных подменяет адреса вкладок. */
  hrefForTab?: (key: CabinetTabKey, defaultHref: string) => string;
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

// ---------- Моё ----------

type CabinetDef = { key: CabinetTabKey; label: string; keywords: readonly string[] };

// Порядок — по спросу с прода (30 дней до 23.09.2026): обзор, достижения,
// пробежки, карта, история, волонтёрство, встречи. На телефоне в ряд чипов
// влезают первые четыре — пусть это будут самые открываемые.
export const CABINET_TABS: readonly CabinetDef[] = [
  {
    key: "dashboard",
    label: "Обзор",
    keywords: ["мой кабинет", "личный кабинет", "профиль", "моя статистика", "дашборд", "главная кабинета"],
  },
  {
    key: "achievements",
    label: "Достижения",
    keywords: ["значки", "награды", "челленджи", "клубы", "юбилей", "ачивки", "майка", "футболка"],
  },
  {
    key: "runs",
    label: "Пробежки",
    keywords: ["мои пробежки", "мои результаты", "мое время", "личный рекорд", "рекорд", "pb", "темп", "забеги"],
  },
  {
    key: "map",
    label: "Карта",
    keywords: ["моя карта", "где бегал", "где я бегал", "туризм", "мои локации", "география"],
  },
  {
    key: "history",
    label: "Моя история",
    keywords: ["история", "хронология", "первый забег", "первая пробежка", "лента", "по годам"],
  },
  {
    key: "volunteering",
    label: "Волонтёрство",
    keywords: ["мои волонтерства", "волонтер", "роли", "помощь", "организация забега"],
  },
  {
    key: "meetings",
    label: "Встречи",
    keywords: ["с кем бегаю", "с кем бегал", "знакомые", "друзья", "попутчики", "соседи по забегу"],
  },
];

const CABINET_SERVICE: readonly CabinetDef[] = [
  { key: "share", label: "Поделиться", keywords: ["сторис", "картинка", "постер", "поделиться результатом"] },
  {
    key: "settings",
    label: "Настройки",
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
    ],
  },
];

export function cabinetHref(ctx: NavContext, key: CabinetTabKey): string {
  const defaultHref =
    key === "share"
      ? PORTAL_CABINET_SHARE_HREF
      : key === "settings"
        ? PORTAL_CABINET_SETTINGS_HREF
        : cabinetTabHref(ctx.user ?? null, key);
  return ctx.hrefForTab ? ctx.hrefForTab(key, defaultHref) : defaultHref;
}

function meSection(ctx: NavContext): NavSection {
  const anon = ctx.user === null;
  const link = (def: CabinetDef): NavLink => ({
    key: def.key,
    label: def.label,
    href: cabinetHref(ctx, def.key),
    keywords: def.keywords,
  });
  return {
    key: "me",
    label: "Мой кабинет",
    shortLabel: anon ? "Войти" : "Моё",
    href: anon ? PORTAL_LOGIN_HREF : cabinetHref(ctx, "dashboard"),
    keywords: ["кабинет", "войти", "вход", "логин", "регистрация"],
    // Анониму пунктов нет: колонка зовёт войти (см. SiteSidebar).
    groups: anon
      ? []
      : [
          { key: "tabs", items: CABINET_TABS.map(link) },
          { key: "service", items: CABINET_SERVICE.map(link) },
        ],
  };
}

// ---------- Организатор ----------

type ToolDef = { key: string; label: string; chipLabel?: string; path: string; keywords: readonly string[] };

// Группы — по тому, зачем организатор приходит: собрать отчёт о субботе,
// посмотреть на людей, спланировать команду. Названия — как на карточках хаба.
const ORGANIZER_TOOL_GROUPS: readonly { key: string; title: string; tools: readonly ToolDef[] }[] = [
  {
    key: "saturday",
    title: "Субботний отчёт",
    tools: [
      { key: "hub", label: "Обзор кабинета", chipLabel: "Обзор", path: "", keywords: ["светофор", "здоровье локации"] },
      { key: "report", label: "Свод по пробежке", chipLabel: "Свод", path: "report", keywords: ["отчет", "свод", "итоги субботы"] },
      { key: "post", label: "Пост-отчёт", chipLabel: "Пост", path: "post", keywords: ["пост", "текст для канала", "анонс", "постер"] },
      { key: "protocols", label: "Протоколы", path: "protocols", keywords: ["скорость протокола", "выгрузка протокола", "задержка"] },
    ],
  },
  {
    key: "people",
    title: "Люди",
    tools: [
      { key: "milestones", label: "Календарь юбилеев", chipLabel: "Юбилеи", path: "milestones", keywords: ["юбилеи", "клуб 50", "клуб 100", "поздравить"] },
      { key: "newcomers", label: "Удержание новичков", chipLabel: "Новички", path: "newcomers", keywords: ["новички", "дебютанты", "первый старт", "вернулись"] },
      { key: "absence", label: "Долгая пауза", chipLabel: "Пауза", path: "absence", keywords: ["пропали", "давно не было", "пауза", "вернуть участников"] },
      { key: "audience", label: "Портрет участника", chipLabel: "Портрет", path: "audience", keywords: ["аудитория", "возраст", "пол", "клубы участников"] },
    ],
  },
  {
    key: "team",
    title: "Команда",
    tools: [
      { key: "volunteers", label: "Волонтёрская скамейка", chipLabel: "Скамейка", path: "volunteers", keywords: ["скамейка", "кого позвать", "резерв волонтеров"] },
      { key: "team", label: "Команда и нагрузка", chipLabel: "Команда", path: "team", keywords: ["нагрузка", "ротация", "организаторы дня"] },
      { key: "attendance", label: "Посещаемость", path: "attendance", keywords: ["явка", "посещаемость", "журнал посещаемости"] },
      { key: "benchmark", label: "Мы и соседи", chipLabel: "Соседи", path: "benchmark", keywords: ["сравнение", "соседние локации", "бенчмарк"] },
    ],
  },
];

export const ORGANIZER_INDEX_HREF = "/organizer";

export function canSeeOrganizer(user: User | null | undefined): boolean {
  return user != null && (user.is_organizer || user.is_admin);
}

function organizerSection(ctx: NavContext): NavSection {
  const place = ctx.organizerLocation;
  const groups: NavGroup[] = place
    ? ORGANIZER_TOOL_GROUPS.map((group) => ({
        key: group.key,
        title: group.title,
        context: true,
        items: group.tools.map((tool) => ({
          key: tool.key,
          label: tool.label,
          chipLabel: tool.chipLabel,
          href: `/organizer/${place.slug}${tool.path ? `/${tool.path}` : ""}`,
          keywords: tool.keywords,
        })),
      }))
    : [
        {
          key: "index",
          items: [{ key: "index", label: "Мои локации", href: ORGANIZER_INDEX_HREF, keywords: ["выбрать локацию"] }],
        },
      ];
  return {
    key: "organizer",
    label: "Кабинет организатора",
    shortLabel: "Орг.",
    href: place ? `/organizer/${place.slug}` : ORGANIZER_INDEX_HREF,
    keywords: ["организатор", "оргкоманда", "кабинет организатора", "директор забега"],
    groups,
  };
}

// ---------- Результаты ----------

function resultsSection(): NavSection {
  return {
    key: "results",
    label: "Результаты",
    shortLabel: "Итоги",
    href: "/results",
    keywords: ["итоги", "результаты"],
    groups: [
      {
        key: "results",
        items: [
          {
            key: "last-results",
            label: "Последние пробежки",
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
 * Страницы одной локации. Нужны и колонке с чипами (когда локация открыта), и
 * поиску: «погода сокол» складывает синоним страницы с найденной локацией.
 */
export const LOCATION_PAGES: readonly {
  key: string;
  label: string;
  chipLabel?: string;
  suffix: string;
  keywords: readonly string[];
  matches?: (pathname: string, base: string) => boolean;
}[] = [
  { key: "location", label: "Обзор", suffix: "", keywords: ["трасса", "как добраться", "описание", "адрес", "старт"] },
  {
    key: "events",
    label: "Журнал протоколов",
    chipLabel: "Протоколы",
    suffix: "/events",
    keywords: ["протоколы", "протокол", "история стартов", "все старты", "архив", "журнал"],
    matches: (pathname, base) => pathname === `${base}/events` || pathname.startsWith(`${base}/protocol/`),
  },
  {
    key: "participants",
    label: "Постоянный состав",
    chipLabel: "Состав",
    suffix: "/participants",
    keywords: ["постоянные участники", "завсегдатаи", "регулярные", "состав", "кто бегает", "сообщество"],
  },
  {
    key: "tops",
    label: "Топы бегунов",
    chipLabel: "Топы",
    suffix: "/tops",
    keywords: ["топ", "лучшие", "самые быстрые", "рекорд трассы", "лидеры", "победители"],
  },
  {
    key: "weather",
    label: "Погода",
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
    href: `${base}${page.suffix}`,
    keywords: page.keywords,
    matches: page.matches ? (pathname: string) => page.matches!(pathname, base) : undefined,
  }));
}

function locationsSection(ctx: NavContext): NavSection {
  const groups: NavGroup[] = [
    {
      key: "catalog",
      items: [
        {
          key: "catalog",
          label: "Все локации",
          href: "/locations",
          keywords: ["каталог", "список локаций", "парки", "где бегать", "карта локаций", "ближайшая локация"],
        },
      ],
    },
  ];
  if (ctx.location) {
    groups.push({ key: "place", title: ctx.location.name, context: true, items: locationPageLinks(ctx.location) });
  }
  return {
    key: "locations",
    label: "Локации",
    shortLabel: "Локации",
    href: "/locations",
    keywords: ["локации", "парки", "места"],
    groups,
  };
}

// ---------- Рейтинги ----------

// Те же группы и названия, что на хабе /ratings: человек видит один и тот же
// рейтинг под одним именем и в меню, и на карточке.
const RATING_GROUPS: readonly { key: string; title: string; items: readonly NavLink[] }[] = [
  {
    key: "runners",
    title: "Бегуны",
    items: [
      { key: "runs", label: "Количество пробежек", chipLabel: "Пробежки", href: "/ratings/runs", keywords: ["больше всех пробежек", "самые активные"] },
      { key: "wins", label: "Первые места", href: "/ratings/wins", keywords: ["победы", "победители", "первое место", "абсолют"] },
      { key: "fastest", label: "Самые быстрые", chipLabel: "Быстрые", href: "/ratings/fastest", keywords: ["быстрее всех", "скорость", "лучшее время", "рекорды времени"] },
    ],
  },
  {
    key: "volunteers",
    title: "Волонтёры",
    items: [
      { key: "volunteering", label: "Количество волонтёрств", chipLabel: "Волонтёрства", href: "/ratings/volunteering", keywords: ["больше всех волонтерств", "волонтеры"] },
      { key: "volunteer-locations", label: "Волонтёрство на разных локациях", chipLabel: "Локации", href: "/ratings/volunteer-locations", keywords: ["волонтерский туризм"] },
      { key: "volunteer-roles", label: "Разнообразие ролей", chipLabel: "Роли", href: "/ratings/volunteer-roles", keywords: ["мультиволонтер", "все роли"] },
    ],
  },
  {
    key: "tourists",
    title: "Туристы",
    items: [
      { key: "locations", label: "Уникальные локации", chipLabel: "Локации", href: "/ratings/locations", keywords: ["туризм", "паркран-туристы", "больше всех локаций", "туристы"] },
      { key: "openings", label: "Открытия локаций", chipLabel: "Открытия", href: "/ratings/openings", keywords: ["первопроходцы", "первый старт локации", "открытие"] },
      { key: "win-locations", label: "Локации с первым местом", chipLabel: "С победой", href: "/ratings/win-locations", keywords: ["победы на разных локациях"] },
      { key: "home-distance", label: "Дальность от дома", chipLabel: "Дальность", href: "/ratings/home-distance", keywords: ["далеко от дома", "километры", "путешествия"] },
    ],
  },
  {
    key: "places",
    title: "Локации",
    items: [
      { key: "location-records", label: "Рекорды локаций", chipLabel: "Рекорды", href: "/ratings/location-records", keywords: ["рекорд трассы", "рекорды", "лучшее время на локации"] },
      { key: "regions", label: "Регионы", href: "/ratings/regions", keywords: ["области", "города", "страны", "регионы россии"] },
    ],
  },
];

export const RATINGS_HUB_HREF = "/ratings";

function ratingsSection(): NavSection {
  return {
    key: "ratings",
    label: "Рейтинги",
    shortLabel: "Рейтинги",
    href: RATINGS_HUB_HREF,
    keywords: ["рейтинг", "лидерборд", "таблица лидеров", "топ", "кто первый"],
    groups: [
      { key: "hub", items: [{ key: "hub", label: "Все рейтинги", chipLabel: "Все", href: RATINGS_HUB_HREF }] },
      ...RATING_GROUPS.map((group) => ({ key: group.key, title: group.title, items: [...group.items] })),
    ],
  };
}

// ---------- О проекте ----------

function projectSection(ctx: NavContext): NavSection {
  const items: NavLink[] = [
    {
      key: "about",
      label: "О проекте",
      href: PORTAL_ABOUT_HREF,
      matches: (pathname) => pathname === PORTAL_ABOUT_HREF,
      keywords: ["о сайте", "контакты", "автор", "приватность", "данные", "политика", "связаться", "поддержка"],
    },
    {
      key: "blog",
      label: "Блог",
      href: PORTAL_BLOG_HREF,
      matches: (pathname) => pathname === PORTAL_BLOG_HREF || pathname.startsWith(`${PORTAL_BLOG_HREF}/`),
      keywords: ["новости", "статьи", "посты", "телеграм канал", "канал"],
    },
    {
      key: "updates",
      label: "Обновления",
      href: PORTAL_UPDATES_HREF,
      keywords: ["что нового", "релизы", "версии", "изменения на сайте"],
    },
    {
      key: "backlog",
      label: "Бэклог идей",
      chipLabel: "Бэклог",
      href: "/backlog",
      keywords: ["идеи", "предложить идею", "предложение", "пожелание", "баг", "ошибка", "сообщить об ошибке"],
    },
  ];
  if (ctx.user?.is_admin) {
    items.push({
      key: "admin",
      label: "Админка",
      href: "/admin/users",
      matches: (pathname) => pathname.startsWith("/admin"),
      tone: "admin",
    });
  }
  return {
    key: "project",
    label: "О проекте",
    shortLabel: "Проект",
    href: PORTAL_ABOUT_HREF,
    groups: [{ key: "project", items }],
  };
}

// ---------- сборка ----------

export function buildSiteNav(ctx: NavContext): NavSection[] {
  const sections: NavSection[] = [meSection(ctx)];
  if (canSeeOrganizer(ctx.user)) {
    sections.push(organizerSection(ctx));
  }
  sections.push(resultsSection(), locationsSection(ctx), ratingsSection(), projectSection(ctx));
  return sections;
}

/**
 * Раздел по адресу страницы — для страниц, которые не передали `active`
 * (главная, блог, «О проекте») и для нижней панели, которая живёт в шапке.
 */
export function sectionKeyFromPath(pathname: string): NavSectionKey | null {
  if (pathname.startsWith("/organizer")) return "organizer";
  if (pathname.startsWith("/results") || pathname.startsWith("/protocol")) return "results";
  if (pathname.startsWith("/locations")) return "locations";
  if (pathname.startsWith("/ratings")) return "ratings";
  if (
    pathname.startsWith(PORTAL_ABOUT_HREF) ||
    pathname.startsWith(PORTAL_BLOG_HREF) ||
    pathname.startsWith(PORTAL_UPDATES_HREF) ||
    pathname.startsWith("/backlog") ||
    pathname.startsWith("/admin")
  ) {
    return "project";
  }
  if (
    pathname.startsWith(PORTAL_CABINET_SETTINGS_HREF) ||
    pathname.startsWith(PORTAL_CABINET_SHARE_HREF) ||
    pathname.startsWith("/new/")
  ) {
    return "me";
  }
  return null;
}

/** Текущий пункт раздела (для подсветки и чипов). */
export function findCurrent(
  section: NavSection,
  pathname: string,
): { group: NavGroup; link: NavLink } | null {
  for (const group of section.groups) {
    for (const link of group.items) {
      if (isLinkCurrent(link, pathname)) {
        return { group, link };
      }
    }
  }
  return null;
}

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
