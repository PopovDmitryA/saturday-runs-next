/**
 * Заголовок вкладки и мета-теги для каждой страницы SPA.
 *
 * Канон — backend/app/services/seo_service.py: тем же набором пользуется
 * серверный пререндер, который отдаёт мета-теги роботам (они JavaScript не
 * ждут). Здесь зеркало для живого браузера; совпадение набора адресов
 * сторожит backend/tests/test_seo_service.py — забыть один из файлов
 * при добавлении роута не выйдет.
 */

import { formatDate } from "./format";

export const SITE_NAME = "run5k.run";

export const DEFAULT_TITLE = "run5k.run — статистика субботних пробежек";
export const DEFAULT_DESCRIPTION =
  "Единая статистика субботних парковых пробежек: 5 вёрст, S95, parkrun и RunPark " +
  "в одном месте. Рекорды и рейтинги участников, страницы локаций, личный кабинет " +
  "с историей стартов и волонтёрства.";

export type PageMeta = {
  title: string;
  description: string;
  /** Попадает ли адрес в индекс поисковика (зеркало indexable на бэкенде). */
  indexable?: boolean;
};

/** Канон статических адресов = STATIC_ROUTES в App.tsx. */
export const STATIC_PAGE_META: Record<string, PageMeta> = {
  "/": { title: DEFAULT_TITLE, description: DEFAULT_DESCRIPTION, indexable: true },
  "/about": {
    title: "О проекте — run5k.run",
    description:
      "Как устроен run5k.run: откуда берутся данные 5 вёрст, S95, parkrun и RunPark, " +
      "что внутри личного кабинета, как проект относится к приватности участников.",
    indexable: true,
  },
  "/blog": {
    title: "Блог — run5k.run",
    description:
      "Разборы и находки из статистики субботних пробежек: рекорды, серии, история " +
      "локаций, необычные достижения участников.",
    indexable: true,
  },
  "/updates": {
    title: "Обновления сайта — run5k.run",
    description: "История релизов run5k.run: что появилось на сайте и когда.",
    indexable: true,
  },
  // «5 верст личный кабинет» — 2472 запроса/мес: за этот запрос боремся,
  // сайт и есть личный кабинет участника (решение Дмитрия 06.08.2026).
  "/login": {
    title: "Личный кабинет участника — вход — run5k.run",
    description:
      "Личный кабинет участника субботних пробежек: история стартов и волонтёрств " +
      "5 вёрст, С95, parkrun и RunPark, рекорды и достижения. Вход через VK или Яндекс.",
    indexable: true,
  },
  // Каталог ловит запросы без названия парка — «5 вёрст карта», «5 вёрст
  // результаты»: перечисляем системы, иначе страница не связывается с ними.
  "/locations": {
    title: "Локации 5 вёрст, С95, parkrun и RunPark — каталог площадок — run5k.run",
    description:
      "Все площадки субботних пробежек на одной карте: сколько было стартов и " +
      "финишей, когда прошёл первый забег, в каких системах живёт локация.",
    indexable: true,
  },
  // Посадочная под запросы «5 вёрст результаты»: свежие протоколы всех площадок.
  "/results": {
    title: "Результаты 5 вёрст, С95 и RunPark — последняя суббота — run5k.run",
    description:
      "Результаты последней субботы по всем паркам: сколько финишёров и волонтёров " +
      "было на каждой площадке, лучшие времена, новички и дата последнего старта.",
    indexable: true,
  },
  "/ratings": {
    title: "Рейтинги — run5k.run",
    description:
      "Сквозные рейтинги участников субботних пробежек по всем системам: пробежки, " +
      "волонтёрство, победы, туризм по локациям.",
    indexable: true,
  },
  "/ratings/runs": {
    title: "Рейтинг по числу пробежек — run5k.run",
    description:
      "Кто пробежал больше всех субботних стартов: сводный рейтинг по 5 вёрстам, " +
      "S95, parkrun и RunPark.",
    indexable: true,
  },
  "/ratings/volunteering": {
    title: "Рейтинг волонтёров — run5k.run",
    description:
      "Кто чаще всех выходил волонтёром на субботние старты — сводный рейтинг по " +
      "всем системам.",
    indexable: true,
  },
  "/ratings/volunteer-roles": {
    title: "Рейтинг по волонтёрским ролям — run5k.run",
    description: "Сколько разных волонтёрских ролей освоили участники субботних пробежек.",
    indexable: true,
  },
  "/ratings/locations": {
    title: "Рейтинг по числу локаций — run5k.run",
    description: "Беговой туризм в цифрах: кто пробежал на наибольшем числе разных локаций.",
    indexable: true,
  },
  "/ratings/volunteer-locations": {
    title: "Рейтинг волонтёрского туризма — run5k.run",
    description: "Кто волонтёрил на наибольшем числе разных локаций субботних пробежек.",
    indexable: true,
  },
  "/ratings/openings": {
    title: "Рейтинг открытий локаций — run5k.run",
    description:
      "Первопроходцы субботних пробежек: кто чаще всех бывал на торжественном открытии " +
      "новых локаций 5 вёрст, С95, parkrun и RunPark.",
    indexable: true,
  },
  "/ratings/wins": {
    title: "Рейтинг побед — run5k.run",
    description: "Кто чаще всех финишировал первым на субботних стартах, с разбивкой по полу.",
    indexable: true,
  },
  "/ratings/home-distance": {
    title: "Рейтинг дальности от дома — run5k.run",
    description:
      "Кто уезжает бегать дальше всех от своей домашней локации: сумма километров " +
      "по уникальным локациям.",
    indexable: true,
  },
  "/ratings/win-locations": {
    title: "Рейтинг побед по локациям — run5k.run",
    description: "На скольких разных локациях участники успевали финишировать первыми.",
    indexable: true,
  },
  "/backlog": {
    title: "Бэклог — run5k.run",
    description: "Что участники предлагают добавить на сайт и за что голосуют.",
  },
  "/new/map-lab": {
    title: "Карта (лаборатория) — run5k.run",
    description: "Экспериментальная карта локаций.",
  },
  "/new/cabinet-preview": {
    title: "Превью кабинета — run5k.run",
    description: "Как выглядит личный кабинет run5k.run — на демонстрационных данных.",
  },
  "/share": {
    title: "Поделиться — run5k.run",
    description: "Картинки с личной статистикой для соцсетей.",
  },
  "/settings": {
    title: "Настройки — run5k.run",
    description: "Настройки профиля и привязанных аккаунтов.",
  },
  "/welcome": {
    title: "Добро пожаловать — run5k.run",
    description: "Найдите себя по имени во всех системах пробежек и привяжите профили.",
  },
  "/oauth/yandex/callback": { title: "Вход — run5k.run", description: "Завершаем вход через Яндекс." },
  "/oauth/vk/callback": { title: "Вход — run5k.run", description: "Завершаем вход через VK." },
};

// Адреса-заглушки: сами ничего не показывают, сразу уводят на другой адрес.
const REDIRECT_PATHS = [
  "/dashboard",
  "/profiles",
  "/runs",
  "/achievements",
  "/co-runners",
  "/volunteering",
  "/maps",
  "/history",
  "/new/dashboard",
  "/new/runs",
  "/new/volunteering",
  "/new/achievements",
  "/new/co-runners",
  "/new/maps",
  "/new/history",
  "/new/share",
  "/new/settings",
  "/sync",
  "/queue",
  "/admin",
];
for (const path of REDIRECT_PATHS) {
  STATIC_PAGE_META[path] = {
    title: "Личный кабинет — run5k.run",
    description: "Переход в личный кабинет.",
  };
}

const ADMIN_META: PageMeta = {
  title: "Админка — run5k.run",
  description: "Служебный раздел run5k.run.",
};

const PROFILE_RE = /^\/users\/([^/]+)(?:\/([^/]+))?$/;
const LOCATION_EVENTS_RE = /^\/locations\/([^/]+)\/events$/;
const LOCATION_PROTOCOL_RE = /^\/locations\/([^/]+)\/protocol\/([^/]+)\/\d{4}-\d{2}-\d{2}$/;
const LOCATION_RE = /^\/locations\/([^/]+)$/;
const SWEEP_HQ_RE = /^\/hq\/.+$/;

/**
 * Страницы, чей заголовок появляется только после загрузки данных (локация и
 * её журнал): Метрика для них шлёт хит отложенно — см. lib/metrika.ts.
 */
export function isLocationEntityPath(rawPath: string): boolean {
  const path = normalizePath(rawPath);
  return LOCATION_RE.test(path) || LOCATION_EVENTS_RE.test(path);
}

export function normalizePath(rawPath: string): string {
  let path = rawPath.split("?")[0].split("#")[0];
  if (!path.startsWith("/")) {
    path = `/${path}`;
  }
  if (path.length > 1) {
    path = path.replace(/\/+$/, "") || "/";
  }
  return path;
}

/** Мета-теги адреса без данных страницы (имя сущности подставляется позже). */
export function resolvePageMeta(rawPath: string): PageMeta {
  const path = normalizePath(rawPath);

  if (path.startsWith("/admin/")) {
    return ADMIN_META;
  }
  if (SWEEP_HQ_RE.test(path)) {
    return {
      title: "Обход parkrun — run5k.run",
      description: "Служебная витрина мирового обхода parkrun.",
    };
  }
  if (path === "/world") {
    return {
      title: "Мировой parkrun — run5k.run",
      description: "Сколько площадок parkrun в мире и как идёт их обход.",
    };
  }
  if (PROFILE_RE.test(path)) {
    // indexable с 15.08.2026: под noindex ВКонтакте и Telegram показывают
    // превью ссылки без картинки. Точные имя и цифры подставляет пререндер
    // (build_profile_meta), здесь — родовой вариант до загрузки данных.
    const [, , tab] = PROFILE_RE.exec(path) ?? [];
    return {
      title: "Участник — run5k.run",
      description:
        "Страница участника субботних пробежек: пробежки, волонтёрство, " +
        "достижения и посещённые локации.",
      // Вкладки профиля в индекс не идут — это срезы той же страницы.
      indexable: tab === undefined,
    };
  }
  if (LOCATION_PROTOCOL_RE.test(path)) {
    return {
      title: "Протокол старта — run5k.run",
      description:
        "Полный протокол старта: места по полу и возрастным группам, личные " +
        "рекорды, дебютанты и волонтёры дня.",
      indexable: true,
    };
  }
  if (LOCATION_EVENTS_RE.test(path)) {
    return {
      title: "Журнал протоколов локации — run5k.run",
      description:
        "Все старты локации: дата, номер события, финишёры, волонтёры и лучшие " +
        "результаты дня.",
      indexable: true,
    };
  }
  if (LOCATION_RE.test(path)) {
    return {
      title: "Локация — run5k.run",
      description:
        "Страница площадки субботних пробежек: рекорды трассы, посещаемость, " +
        "история систем и распределение финишных времён.",
      indexable: true,
    };
  }

  return STATIC_PAGE_META[path] ?? { title: DEFAULT_TITLE, description: DEFAULT_DESCRIPTION };
}

/**
 * Разбивка по разрядам неразрывным пробелом: 21581 → «21 581».
 * Зеркало _num из seo_service.py — тексты страницы и пререндера обязаны
 * совпадать символ в символ.
 */
function num(value: number): string {
  return value.toLocaleString("ru-RU");
}

function plural(count: number, one: string, few: string, many: string): string {
  const tail100 = count % 100;
  if (tail100 >= 11 && tail100 <= 14) {
    return many;
  }
  const tail = count % 10;
  if (tail === 1) return one;
  if (tail >= 2 && tail <= 4) return few;
  return many;
}

/** 00:17:51 → 17:51 — зеркало _strip_leading_hours из seo_service.py. */
function stripLeadingHours(display: string | null | undefined): string | null {
  if (!display) {
    return null;
  }
  return display.startsWith("00:") ? display.slice(3) : display;
}

/** Как система называется в заголовке. Зеркало PLATFORM_LABELS из seo_service.py. */
const PLATFORM_LABELS: Record<string, string> = {
  five_verst: "5 вёрст",
  s95: "С95",
  parkrun: "parkrun",
  runpark: "RunPark",
};

const TITLE_BUDGET = 70;
const DESCRIPTION_BUDGET = 160;

type LocationMetaPlatform = {
  platform_code: string;
  is_active?: boolean | null;
  events_count?: number;
  last_event_date?: string | null;
};

type LocationMetaSource = {
  name: string;
  city?: string | null;
  platforms?: LocationMetaPlatform[] | null;
  stats?: {
    events_count?: number;
    finishers_total?: number;
    course_records?: {
      male?: { finish_time_display?: string | null } | null;
      female?: { finish_time_display?: string | null } | null;
    } | null;
  } | null;
};

/**
 * Мета-теги локации по загруженным данным — зеркало build_location_meta
 * из seo_service.py. Меняете формулировку — меняйте в обоих местах.
 */
export function locationPageMeta(
  payload: LocationMetaSource,
  options: { eventsLog?: boolean } = {},
): PageMeta {
  const name = payload.name || "Локация";
  const city = payload.city ?? null;
  const platform = activePlatformLabel(payload);
  const where = locationHeadline(name, city, platform);

  const stats = payload.stats ?? {};
  const parts: string[] = [];
  const events = stats.events_count ?? 0;
  const finishers = stats.finishers_total ?? 0;
  if (events) {
    parts.push(`${num(events)} ${plural(events, "старт", "старта", "стартов")}`);
  }
  if (finishers) {
    parts.push(`${num(finishers)} ${plural(finishers, "финиш", "финиша", "финишей")}`);
  }
  const male = stripLeadingHours(stats.course_records?.male?.finish_time_display ?? null);
  const female = stripLeadingHours(stats.course_records?.female?.finish_time_display ?? null);
  if (male || female) {
    parts.push(`рекорды трассы ${[male, female].filter(Boolean).join(" / ")}`);
  }
  const numbers = parts.join(", ");

  // Описание держим в 160 символах: длиннее поисковик обрежет многоточием.
  if (options.eventsLog) {
    // «журнал протоколов» — часть head, а не хвост: только он отличает эту
    // страницу от основной, и отбрасывать его нельзя.
    return {
      title: fitTitle(`${where}: журнал протоколов`),
      description: describe(
        `Все старты локации «${name}»`,
        numbers,
        ". Дата, номер события, финишёры, волонтёры и лучшее время дня.",
        ". Дата, номер, финишёры и лучшее время дня.",
      ),
      indexable: true,
    };
  }

  // Систему называем и в описании — один раз и по-человечески, подлежащим,
  // а не списком ключевых слов.
  let lead = platform ? `${platform}, «${name}»` : `Локация «${name}»`;
  if (city) {
    lead += ` (${city})`;
  }

  return {
    title: fitTitle(where, " — результаты и статистика", " — результаты"),
    description: describe(
      lead,
      numbers,
      ". Результаты субботних забегов, посещаемость и рейтинги участников.",
      ". Результаты забегов и рейтинги участников.",
    ),
    indexable: true,
  };
}

/** Заголовок протокола: имя, дата и номер уже известны из ответа API. */
export function locationProtocolMeta(payload: {
  name: string;
  city: string | null;
  platform_code: string;
  event_date: string;
  event_number: number | null;
  summary: { finishers: number; volunteers: number; best_time_display: string | null };
}): PageMeta {
  const name = payload.name || "Локация";
  const platform = PLATFORM_LABELS[payload.platform_code] ?? payload.platform_code;
  const day = formatDate(payload.event_date);
  const numberPart = payload.event_number ? ` №${payload.event_number}` : "";
  const parts: string[] = [];
  if (payload.summary.finishers) {
    parts.push(
      `${num(payload.summary.finishers)} ${plural(payload.summary.finishers, "финишёр", "финишёра", "финишёров")}`,
    );
  }
  if (payload.summary.volunteers) {
    parts.push(
      `${num(payload.summary.volunteers)} ${plural(payload.summary.volunteers, "волонтёр", "волонтёра", "волонтёров")}`,
    );
  }
  return {
    title: fitTitle(`${name}${numberPart} — протокол ${day}`),
    description: describe(
      `Протокол старта ${platform} «${name}» ${day}`,
      parts.join(", "),
      ". Места по полу и возрастным группам, личные рекорды и дебютанты.",
      ". Места, рекорды и дебютанты дня.",
    ),
    indexable: true,
  };
}

/**
 * Название текущей системы локации — зеркало _active_platform_label.
 * Именно связку «5 вёрст + парк» набирают в поиске.
 */
function activePlatformLabel(payload: LocationMetaSource): string | null {
  const platforms = payload.platforms ?? [];
  if (platforms.length === 0) {
    return null;
  }
  const active = platforms.find((p) => p.is_active);
  if (active) {
    return PLATFORM_LABELS[active.platform_code] ?? null;
  }
  // Действующей нет (локация закрыта) — называем последнюю по дате.
  const newest = platforms.reduce((best, p) =>
    (p.last_event_date ?? "") > (best.last_event_date ?? "") ? p : best,
  );
  return PLATFORM_LABELS[newest.platform_code] ?? null;
}

/** «5 вёрст Мещерское озеро, Нижний Новгород» — зеркало _location_headline. */
function locationHeadline(name: string, city: string | null, platform: string | null): string {
  let head = platform ? `${platform} ${name}` : name;
  const cityText = (city ?? "").trim();
  // Город не приписываем, если он уже внутри названия («Томск Сосновый Бор»).
  if (cityText && !head.toLowerCase().includes(cityText.toLowerCase())) {
    head = `${head}, ${cityText}`;
  }
  return head;
}

/** Заголовок под TITLE_BUDGET — зеркало _fit_title. */
function fitTitle(head: string, ...tails: string[]): string {
  const brand = ` — ${SITE_NAME}`;
  for (const tail of [...tails, ""]) {
    const candidate = `${head}${tail}${brand}`;
    if (candidate.length <= TITLE_BUDGET) {
      return candidate;
    }
  }
  // Не влезло даже с брендом — домен и так виден в выдаче строкой адреса.
  return head;
}

/** Описание под DESCRIPTION_BUDGET — зеркало _describe. */
function describe(lead: string, numbers: string, ...tails: string[]): string {
  const head = numbers ? `${lead}: ${numbers}` : lead;
  for (const tail of tails) {
    const candidate = `${head}${tail}`;
    if (candidate.length <= DESCRIPTION_BUDGET) {
      return candidate;
    }
  }
  const withDot = head.endsWith(".") ? head : `${head}.`;
  if (withDot.length <= DESCRIPTION_BUDGET) {
    return withDot;
  }
  const cut = withDot.slice(0, DESCRIPTION_BUDGET);
  const dot = cut.lastIndexOf(". ");
  return dot > DESCRIPTION_BUDGET / 2 ? cut.slice(0, dot + 1) : `${cut.replace(/[ ,;:—-]+$/, "")}…`;
}

/**
 * Вводные предложения о локации — зеркало location_lead_sentences.
 * Текст обязан совпадать с серверным: иначе робот и человек видят разное.
 */
export function locationLeadSentences(payload: LocationMetaSource): string[] {
  const name = payload.name || "Локация";
  const city = (payload.city ?? "").trim();
  const platform = activePlatformLabel(payload);
  const stats = payload.stats ?? {};

  const where = city ? `«${name}» (${city})` : `«${name}»`;
  const sentences = [
    platform
      ? `${where} — площадка субботних пробежек ${platform}.`
      : `${where} — площадка субботних пробежек.`,
  ];

  const events = stats.events_count ?? 0;
  const finishers = stats.finishers_total ?? 0;
  if (events && finishers) {
    sentences.push(
      `Здесь прошло ${num(events)} ${plural(events, "старт", "старта", "стартов")}, ` +
        `финишировали ${num(finishers)} ${plural(finishers, "участник", "участника", "участников")}.`,
    );
  }

  // У локации может быть несколько эпох: parkrun → RunPark → 5 вёрст.
  const previous = (payload.platforms ?? [])
    .filter((p) => !p.is_active && (p.events_count ?? 0) > 0)
    .map((p) => PLATFORM_LABELS[p.platform_code])
    .filter(Boolean);
  if (platform && previous.length > 0) {
    const joined =
      previous.length > 1
        ? `${previous.slice(0, -1).join(", ")} и ${previous[previous.length - 1]}`
        : previous[0];
    sentences.push(`До ${platform} старты здесь проводили ${joined}.`);
  }

  return sentences;
}

function setMetaTag(selector: string, attr: "name" | "property", key: string, value: string) {
  let tag = document.head.querySelector<HTMLMetaElement>(selector);
  if (!tag) {
    tag = document.createElement("meta");
    tag.setAttribute(attr, key);
    document.head.appendChild(tag);
  }
  tag.setAttribute("content", value);
}

/** Проставляет заголовок вкладки и мета-теги текущей страницы. */
export function applyPageMeta(meta: PageMeta): void {
  document.title = meta.title;
  setMetaTag('meta[name="description"]', "name", "description", meta.description);
  setMetaTag('meta[name="robots"]', "name", "robots", meta.indexable ? "index,follow" : "noindex,follow");
  setMetaTag('meta[property="og:title"]', "property", "og:title", meta.title);
  setMetaTag('meta[property="og:description"]', "property", "og:description", meta.description);
  setMetaTag('meta[property="og:site_name"]', "property", "og:site_name", SITE_NAME);
  setMetaTag('meta[property="og:type"]', "property", "og:type", "website");
  setMetaTag('meta[property="og:url"]', "property", "og:url", window.location.href);
  // Дефолтная брендовая картинка. Роботы мессенджеров сюда не смотрят (они
  // получают пререндер с точным og:image от бэкенда) — это для JS-краулеров.
  setMetaTag(
    'meta[property="og:image"]',
    "property",
    "og:image",
    `${window.location.origin}/og/default.png`,
  );

  let canonical = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]');
  if (!canonical) {
    canonical = document.createElement("link");
    canonical.rel = "canonical";
    document.head.appendChild(canonical);
  }
  canonical.href = `${window.location.origin}${normalizePath(window.location.pathname)}`;
}
