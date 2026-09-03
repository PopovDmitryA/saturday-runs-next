/** Public-facing site name and assets (Russian branding). */
export const SITE_NAME = "Статистика парковых пробежек";

export const SITE_LOGO_SRC = "/logo.png";
export const SITE_FAVICON_SRC = "/favicon.png";

export const SITE_HOME_HREF = "/dashboard";
export const SITE_PUBLIC_HOME_HREF = "/";

/**
 * Старый сайт статистики (Grafana) закрыт: grafana.run5k.run отдаёт заглушку
 * «дашборды переехали» (deploy/grafana/farewell/index.html). Поэтому здесь
 * больше нет ссылок ТУДА — только карта «какой дашборд куда переехал».
 */
export const LEGACY_SITE_LABEL = "grafana.run5k.run";

/** Paths that belonged to Grafana when it was served from apex run5k.run. */
export function isLegacyGrafanaPath(path: string): boolean {
  return (
    path.startsWith("/d/")
    || path.startsWith("/dashboard/")
    || path.startsWith("/public/")
  );
}

/**
 * Куда переехал дашборд: uid из старого адреса /d/<uid>/<slug> → адрес на сайте.
 * Тот же список, что на прощальной странице (deploy/grafana/farewell/index.html);
 * дашборды без аналога (клубы, «Статистика 2024», карта стартов 1 января) сюда
 * сознательно не попадают — по ним человек увидит объяснение, а не переход.
 */
const LEGACY_DASHBOARD_ROUTES: Record<string, string> = {
  ce5xtszxy4074e: "/", // ГЛАВНАЯ
  beb3dpef24r28a: "/ratings/runs", // Рейтинг количества пробежек
  feb3hdye0fhtse: "/ratings/volunteering", // Рейтинг количества волонтерств
  fehx3pjkvj56oa: "/ratings/locations", // Уникальные локации
  feitbfpcwwb28a: "/ratings/wins", // Рейтинг победителей
  deprgii19fdoga: "/ratings/fastest", // Рейтинг по времени финиша
  "d615a771-0ea5-4559-ac97-536e08662a96": "/ratings/location-records", // Возрастные рекорды
  "f813e098-c800-412d-b177-8e85a8521d69": "/ratings/openings", // Туристический рейтинг открытий
  dekvyyrwadwjkb: "/ratings/home-distance", // Расстояние посещённых локаций
  be51fcfw6ejggb: "/ratings/regions", // Локаций 5 вёрст по регионам
  eednttn3wos1sf: "/ratings/locations", // Прогноз завершения туризма
  de1hu8dabny80c: "/ratings/locations", // Карта туристов
  de96ruht0r0n4c: "/ratings/volunteer-locations", // Карта туристов-волонтёров
  "b5142ff6-1991-4c8c-b432-7c69fb27fbed": "/ratings/runs?view=journal", // Журнал посещаемости пробежек
  "2c979df9-0a15-42ee-83a9-e46ecf318027": "/ratings/locations?view=journal", // Журнал туризма
  "65e77df2-2222-4167-8e11-929a081e9553": "/locations", // Журнал посещаемости локации
  bepnuz4ecveo0f: "/locations", // Статистика по локациям
  ae5xf2cebu3gga: "/locations", // Рейтинг участников и волонтёров внутри локации
  "55057462-8a8b-4ebb-96ae-bad7ef356a3d": "/locations", // Рекорды посещаемости локаций
  eeqquzpgqp88wd: "/locations", // Календарь первых стартов 5 вёрст
  de6y9kfuym8sgf: "/locations", // Карта всех систем парковых забегов
  "4a385e6f-5cb6-4e7d-914f-8fbee0b34bba": "/protocol", // Единый протокол
  "4469d2d0-c3ec-487b-85cf-190526e52993": "/maps", // Карта моих стартов
  "86bf8188-e70b-4e14-8997-6a8893142f55": "/co-runners", // Счёт по личным встречам
  "3e54a2d8-ef9f-4743-8117-4a2ddb47d6a7": "/achievements", // Челленджи
  "516a514a-fe34-4cbd-baa7-2f87cd5464a0": "/organizer", // Свод по пробежке для отчётов
  "cea88eb2-47e4-4334-bfd6-e13ad11f5e3a": "/organizer", // Долгая пауза
};

export function legacyGrafanaTarget(path: string): string | null {
  const uid = /^\/d\/([\w-]+)/.exec(path)?.[1];
  return (uid && LEGACY_DASHBOARD_ROUTES[uid]) || null;
}
