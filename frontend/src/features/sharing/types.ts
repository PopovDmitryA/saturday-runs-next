// Движок «Поделиться 2.0».
//
// Развязка «что показываем» от «как рисуем»:
//  - ShareSubject — сюжет с данными (веха, пробежка, сводка, локация, рейтинг);
//    адаптеры в subjects.ts переводят API-типы в нормализованный ShareCardData.
//  - Карточки (cards/*.tsx) рендерят ShareCardData в выбранном формате и луке.
//  - Новый сюжет = один адаптер данных, правок рендера не требует.
//
// Постеры уходят наружу (мессенджеры, сториз), поэтому их палитра ФИКСИРОВАНА
// и от темы сайта не зависит — см. looks.ts.

export type ShareFormatId = "story" | "square" | "wide";

export type ShareFormat = {
  id: ShareFormatId;
  width: number;
  height: number;
  label: string;
  hint: string;
};

export const SHARE_FORMATS: ShareFormat[] = [
  { id: "story", width: 1080, height: 1920, label: "Сториз", hint: "9:16 · Instagram, статусы" },
  { id: "square", width: 1080, height: 1080, label: "Квадрат", hint: "1:1 · лента, чаты" },
  { id: "wide", width: 1200, height: 630, label: "Широкий", hint: "16:9 · Telegram-каналы" },
];

export function shareFormat(id: ShareFormatId): ShareFormat {
  return SHARE_FORMATS.find((format) => format.id === id) ?? SHARE_FORMATS[0];
}

/** Одна метрика на постере: крупное значение + подпись. */
export type ShareMetric = {
  id: string;
  value: string;
  label: string;
  /**
   * Подписи плиток набраны капсом. Имя рекордсмена в капсе читается как крик и
   * хуже разбирается — такие подписи оставляем как есть.
   */
  keepLabelCase?: boolean;
};

/** Эпоха таймлайна систем для «Визитки» локации. */
export type ShareTimelineEntry = {
  label: string;
  period: string;
  current: boolean;
};

/**
 * Нормализованные данные постера. Всё опциональное: каждая карточка рисует
 * те поля, которые знает, и молча пропускает отсутствующие.
 */
export type ShareCardData = {
  /** Ветка палитры: личные сюжеты — индиго, локации — бирюза. */
  audience: "runner" | "location";
  /** Имя участника или «Локация · Город». */
  title: string;
  /** Строка под именем: дата, период, номер старта. */
  subtitle?: string;
  /** Плашка-акцент («ВЕХА · КЛУБ 50», «ПОСЛЕДНИЙ СТАРТ»). */
  plate?: string;
  /** Герой карточки: большая цифра или время. */
  hero?: { value: string; caption?: string };
  /** Яркий чип под героем («Личный рекорд», «Рекорд посещаемости»). */
  chip?: string;
  /**
   * Метрики-кандидаты в порядке приоритета. Карточка рисует первые N по
   * своему лимиту; пользователь может заменить набор в «Настроить».
   */
  metrics: ShareMetric[];
  /** Строка-факт мелким кеглем («4 года · 12 локаций · 3 региона»). */
  fact?: string;
  /** Таймлайн систем (визитка локации). */
  timeline?: ShareTimelineEntry[];
  /** Мини-календарь суббот: true = была активность. */
  heat?: boolean[];
};

/**
 * Сюжет шаринга. subjectKey уходит в аналитику (share_open …), поэтому
 * значения короткие и стабильные.
 */
export type ShareSubjectKind =
  | "milestone"
  | "run"
  | "volunteering"
  | "summary"
  | "location_event"
  | "location_protocol"
  | "location_card"
  | "location_me"
  | "rating";

export type ShareSubject = {
  kind: ShareSubjectKind;
  data: ShareCardData;
  /** Имя файла при скачивании, без расширения. */
  fileName: string;
  /** Формат по умолчанию: личные — story, локации — wide (Telegram-каналы). */
  defaultFormat: ShareFormatId;
};

/** Точка входа для аналитики: откуда открыли шторку. */
export type ShareEntryPoint =
  | "dashboard"
  | "runs"
  | "volunteering"
  | "history"
  | "on_this_day"
  | "location"
  | "rating"
  | "gallery";
