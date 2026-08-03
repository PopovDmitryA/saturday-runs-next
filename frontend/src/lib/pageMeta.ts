/**
 * Заголовок вкладки и мета-теги для каждой страницы SPA.
 *
 * Канон — backend/app/services/seo_service.py: тем же набором пользуется
 * серверный пререндер, который отдаёт мета-теги роботам (они JavaScript не
 * ждут). Здесь зеркало для живого браузера; совпадение набора адресов
 * сторожит backend/tests/test_seo_service.py — забыть один из файлов
 * при добавлении роута не выйдет.
 */

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
  "/login": {
    title: "Вход — run5k.run",
    description: "Вход в личный кабинет run5k.run через VK или Яндекс.",
  },
  "/locations": {
    title: "Локации — run5k.run",
    description:
      "Каталог площадок субботних пробежек: сколько было стартов и финишей, когда " +
      "прошёл первый забег, в каких системах живёт локация.",
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
    description: "Беговой туризм в цифрах: кто пробежал на наибольшем числе разных площадок.",
    indexable: true,
  },
  "/ratings/volunteer-locations": {
    title: "Рейтинг волонтёрского туризма — run5k.run",
    description: "Кто волонтёрил на наибольшем числе разных площадок субботних пробежек.",
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
      "по уникальным площадкам.",
    indexable: true,
  },
  "/ratings/win-locations": {
    title: "Рейтинг побед по локациям — run5k.run",
    description: "На скольких разных площадках участники успевали финишировать первыми.",
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
const LOCATION_RE = /^\/locations\/([^/]+)$/;
const SWEEP_HQ_RE = /^\/hq\/.+$/;

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
    return {
      title: "Участник — run5k.run",
      description:
        "Страница участника субботних пробежек: пробежки, волонтёрство, " +
        "достижения и посещённые локации.",
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

type LocationMetaSource = {
  name: string;
  city?: string | null;
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
  const where = city ? `${name}, ${city}` : name;

  const stats = payload.stats ?? {};
  const parts: string[] = [];
  const events = stats.events_count ?? 0;
  const finishers = stats.finishers_total ?? 0;
  if (events) {
    parts.push(`${events} ${plural(events, "старт", "старта", "стартов")}`);
  }
  if (finishers) {
    parts.push(`${finishers} ${plural(finishers, "финиш", "финиша", "финишей")}`);
  }
  const male = stripLeadingHours(stats.course_records?.male?.finish_time_display ?? null);
  const female = stripLeadingHours(stats.course_records?.female?.finish_time_display ?? null);
  if (male || female) {
    parts.push(`рекорды трассы ${[male, female].filter(Boolean).join(" / ")}`);
  }
  const numbers = parts.join(", ");

  // Описание держим в ~155 символах: длиннее поисковик обрежет многоточием.
  if (options.eventsLog) {
    let description = `Все старты локации «${name}»`;
    if (numbers) {
      description += `: ${numbers}`;
    }
    description += ". Дата, номер события, финишёры, волонтёры и лучшее время дня.";
    return { title: `${where}: журнал протоколов — ${SITE_NAME}`, description, indexable: true };
  }

  let description = `Локация «${name}»`;
  if (city) {
    description += ` (${city})`;
  }
  if (numbers) {
    description += `: ${numbers}`;
  }
  description += ". Посещаемость, история систем и рейтинги участников.";

  return {
    title: `${where} — статистика субботних пробежек — ${SITE_NAME}`,
    description,
    indexable: true,
  };
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
