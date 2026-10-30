// API планировщика забегов. Отдельный модуль, а не lib/api.ts — тот файл
// одновременно правят соседние ветки (паттерн features/settings/accountDeletionApi.ts).
import { apiFetch } from "../../lib/api";

// План — «планирую пробежать» на локации в дату; что человек будет делать на
// старте, не спрашиваем (решение Дмитрия 25.10.2026). Время старта сервер
// по-прежнему отдаёт (start_time), но планировщик его не показывает — план про
// дату (решение Дмитрия 26.10.2026), поэтому в типах его нет.

/** Старт в календаре локации: суббота или особая дата из админки. */
export type StartDateItem = {
  date: string; // YYYY-MM-DD
  kind: "regular" | "special";
  /** Название особой даты («Новогодний старт»); у суббот — null. */
  title: string | null;
  platform_code: string;
  /** ≈№ старта; null — прогноза нет (давно не бегали, нет номера). */
  number_forecast: number | null;
  cancelled: boolean;
  cancel_reason: string | null;
};

export type PlanLocation = {
  key: string;
  slug: string;
  name: string;
  platform_code: string | null;
};

/**
 * Что известно про прошедший план по протоколам — видит только владелец.
 * «volunteered» — собирался бежать, а в протоколе волонтёр этой локации:
 * на старте он был. У прошедшего дня с «Не бегаю»: «ran» — всё-таки
 * пробежал (где угодно), «rested» — не бегал.
 */
export type PlanFact = {
  /**
   * elsewhere — только у вхождения постоянного плана: здесь не был, а
   * пробежал в другой локации (location). missed у вхождения страница не
   * красит: серых отметок у постоянного плана нет.
   */
  status: "ran" | "volunteered" | "missed" | "unknown" | "rested" | "elsewhere";
  finish_time_sec: number | null;
  event_number: number | null;
  /** Где пробежал — только у elsewhere. */
  location?: { name: string; slug: string } | null;
};

/** Ссылка на постоянный план у вхождения и у разового плана на его же локацию. */
export type PlanSeriesRef = { id: string };

/** Постоянный план, чью субботу вытеснил разовый план в другой локации. */
export type PlanDisplaces = { id: string; location: PlanLocation };

/**
 * План на локацию (kind="plan") или строка дня без локации (kind="day") —
 * отметка «Не бегаю». Заметок нет (решение Дмитрия 27.10.2026).
 */
export type PlanItem = {
  /**
   * Строка плана или строка дня; null — вхождение постоянного плана: строки
   * нет, убрать его — «Не бегаю» на этот день или остановить постоянный план.
   */
  id: string | null;
  /**
   * Стабильный ключ: «plan:<uuid>» у строк, «series:<uuid>:<YYYY-MM-DD>» у
   * вхождений — им ключуются списки и сравнения, а не id.
   */
  ref: string;
  plan_date: string;
  kind: "plan" | "day";
  /**
   * Только у строки дня: человек отметил «Не бегаю». На локации и в
   * уведомлениях этого нет; публичная отметка видна в календаре профиля.
   */
  rest: boolean;
  /**
   * Публичный план (или публичная отметка «Не бегаю»): имя на странице
   * локации и план в календаре профиля. Выбирают для каждого плана, у
   * вхождения постоянного — как у самого постоянного. Имя всё равно не
   * показывается, если профиль закрыт или человек — ребёнок.
   */
  is_public: boolean;
  location: PlanLocation | null;
  start: {
    kind: "regular" | "special";
    title: string | null;
    number_forecast: number | null;
    cancelled: boolean;
    cancel_reason: string | null;
  } | null;
  past: boolean;
  fact: PlanFact | null;
  /**
   * Вхождение постоянного плана (id === null) или разовый план на его же
   * локацию, поглотивший субботу (id есть): для чужих глаз — «↻ Каждую субботу».
   */
  series?: PlanSeriesRef | null;
  /** Разовый план в другой локации, вытеснивший субботу постоянного. */
  displaces?: PlanDisplaces | null;
};

/** Вхождение постоянного плана: строки нет — убрать его можно «Не бегаю» на день или остановкой плана. */
export function isSeriesOccurrence(item: PlanItem): boolean {
  return item.id === null && Boolean(item.series);
}

/**
 * Постоянный план «каждую субботу» на одной локации. Правило, а не строки:
 * вхождения сайт считает сам при каждом чтении, исключения — «Не бегаю» на
 * день и разовый план в другой локации на эту дату.
 */
export type PlanSeries = {
  id: string;
  location: PlanLocation;
  /** Первая дата правила. */
  starts_on: string;
  /**
   * null — «пока не остановите»; иначе последняя дата включительно (срок
   * ставило прежнее окно, нынешнее его не задаёт).
   */
  until: string | null;
  /** Срок кончился (until < сегодня) — «Продлить»; через 30 дней план остановится сам. */
  expired: boolean;
  /** Локация сейчас проводит старты; нет — будущих суббот у плана нет. */
  plannable: boolean;
  /**
   * На паузе: три старта подряд человека на локации не было (без привязок к
   * системам локации — давно не заходил на сайт), и имени на будущих датах
   * нет. У кончившегося срока — false: там «Продлить».
   */
  sleeping: boolean;
  sleeping_since: string | null;
  /**
   * Пробежит или поволонтёрит там — план продолжится сам (есть привязка к
   * системам локации). false — протокол его не увидит, только «Продолжить».
   */
  wakes_by_presence: boolean;
  /** Ближайшее вхождение с учётом «Пропущу» и поездок; на паузе — null. */
  next_date: string | null;
  /** Будущие «Не бегаю» на датах плана. */
  skipped: string[];
  /** Будущие поездки, вытеснившие субботу плана. */
  away: { date: string; location: PlanLocation }[];
  /** Публичность — одна на весь постоянный план. */
  is_public: boolean;
};

/** «Вы пробежали здесь 9 из 10 последних стартов» — предложить постоянный план. */
export type SeriesHint = { location: PlanLocation; runs: number; of: number };

export type MySeries = { series: PlanSeries | null; hint: SeriesHint | null };

/** Дата правила в окне «каждую субботу» до сохранения. */
export type SeriesPreviewDate = {
  date: string;
  kind: "regular" | "special";
  title: string | null;
  number_forecast: number | null;
  /**
   * on — встанет в план; skipped — стоит «Не бегаю»; away — там разовый план
   * в другой локации (other); cancelled — старт отменён.
   */
  state: "on" | "skipped" | "away" | "cancelled";
  other: PlanLocation | null;
};

export type SeriesPreview = {
  key: string;
  starts_on: string;
  until: string | null;
  /** Все даты правила в сроке (без срока — на год). */
  total: number;
  /** Сколько из них встанет (state=on). */
  on: number;
  dates: SeriesPreviewDate[];
  /**
   * Как план выйдет из паузы: протоколом (есть привязка к системе, что
   * проводит здесь старты) или только «Продолжить».
   */
  wakes_by_presence?: boolean;
};

export type SeriesSaveBody = {
  key: string;
  /** Первая дата; нет — ближайшая. */
  starts_on?: string | null;
  /** null — «пока не остановлю». */
  until?: string | null;
  /** Снятые галочки ближайших суббот — «Не бегаю» на эти даты; возвращённые — снять. */
  skip?: string[];
  unskip?: string[];
  /** Заменить постоянный план на другой локации (после 409). */
  replace?: boolean;
  /** Первая дата занята разовым планом в другой локации — убрать его. */
  replace_first?: boolean;
  /** Публичность всего плана. Поля нет — у нового как у профиля, у правки прежняя. */
  is_public?: boolean;
};

export type SeriesSaveResult = {
  series: PlanSeries;
  /** Постоянный план на другой локации, который этот заменил. */
  replaced: PlanLocation | null;
  /** Даты skip, которые не отметились. */
  skipped_failed: string[];
};

export type PlanSaveResult = {
  plan: PlanItem;
  /** Постоянный план, чью субботу вытеснил этот план; иначе null. */
  displaced?: PlanLocation | null;
  /** План на локацию своего постоянного, а тот на паузе — предложить «Продолжить». */
  series_sleeping?: boolean;
};

/**
 * Знаковый старт человека в эту дату — для оргкабинета. Подпись короткая и
 * готовая («250-я пробежка», «100-я у нас», «впервые у нас»): её собирает
 * сервер по правилам сайта, фронт только красит по kind.
 */
export type PlanMarkKind = "first_ever" | "jubilee" | "jubilee_here" | "jubilee_platform" | "first_here";

export type PlanMark = { kind: PlanMarkKind; label: string };

/**
 * Человек с публичным планом. На странице локации рядом с именем — только
 * всего пробежек в скобках, «Анна Смирнова (132)»: метки «впервые у нас» и
 * юбилеи сервер туда больше не отдаёт (решение Дмитрия 25.10.2026 — блок для
 * участников компактный, знаковые старты видит организатор). first_here и
 * jubilee приходят только в оргкалендаре.
 */
export type PlanPerson = {
  name: string;
  /** /users/{handle}; null — без ссылки. */
  handle: string | null;
  /** Всего пробежек по всем системам, как в профиле; null — посчитать не удалось. */
  runs: number | null;
  /** Оргкалендарь: до этого старта ни пробежки, ни волонтёрства на этой локации. */
  first_here?: boolean;
  /** Оргкалендарь: юбилейный номер, которым станет запланированная пробежка (100); иначе null. */
  jubilee?: number | null;
  /** Оргкалендарь: знаковые старты списком, если сервер отдаёт их так. */
  marks?: PlanMark[];
  /** Блок локации: постоянный план сюда — у имени значок ↻. */
  regular?: boolean;
};

/** Кто планирует пробежать в дату: имена открытых планов и остальные числом. */
export type PlanAudience = {
  /** Открыли планы: имя как в профиле. */
  people: PlanPerson[];
  /** Приватные планы и закрытые профили — только числом. */
  private: number;
  /** Публичный план и открытый профиль, но по протоколам дети — числом, без имени. */
  children: number;
};

/** Группа планирующих на дату: total = people.length + private + children. */
export type PlanAudienceGroup = PlanAudience & { total: number };

/**
 * Дата с планами в блоке локации: разовые и постоянные одним списком (у
 * постоянных — regular), total — все вместе.
 */
export type LocationPlansDate = PlanAudience & {
  date: string;
  kind: "regular" | "special";
  title: string | null;
  number_forecast: number | null;
  total: number;
};

export type PlansElsewhereDate = {
  date: string;
  locations: PlanLocation[];
  /** Лимит дня исчерпан — сюда на эту дату план не поставить. */
  full: boolean;
  /**
   * Сколько планов можно на этот день. Планов больше (админ убрал особую
   * дату, объединение профилей) — заменой одного не уложиться: сервер на
   * replace_key ответит 409, лишние убирают в «Моих планах».
   */
  limit?: number;
};

export type LocationPlansBlock = {
  identity_key: string;
  plannable: boolean;
  /** Почему нельзя запланировать (parkrun, пауза, воскресный старт…). */
  reason: string | null;
  /** «Скоро открытие»: планировать можно, дату открытия сверить на сайте системы. */
  upcoming?: boolean;
  /** Страница локации на сайте системы — туда окно отправляет сверить дату. */
  official_url?: string | null;
  dates: LocationPlansDate[];
  /**
   * Планы зрителя на этой локации: все разовые и вхождения
   * постоянного на даты вкладок; гостю — [].
   */
  mine: PlanItem[];
  /**
   * Разовые планы зрителя в ДРУГИХ локациях на год вперёд: окно плана
   * подписывает такие даты «бегу в …», заполненный день (full) не нажимается.
   */
  elsewhere?: PlansElsewhereDate[];
  /** Постоянный план зрителя на ЭТОЙ локации; нет или он на другой — null. */
  my_series?: PlanSeries | null;
  /** Подсказка «9 из 10» — только если её локация эта, а постоянного плана нет. */
  series_hint?: SeriesHint | null;
};

export type LocationStartDates = {
  identity_key: string;
  plannable: boolean;
  reason: string | null;
  dates: StartDateItem[];
};

export type MyPlans = { today: string; items: PlanItem[] };

/** Ближайший день старта (эта суббота или особая дата раньше неё) и что на нём у человека. */
export type NextStartDay = {
  date: string; // YYYY-MM-DD
  kind: "regular" | "special";
  title: string | null;
  /** На этот день есть план пробежки. */
  planned: boolean;
  /** План дня — постоянный («↻ каждую субботу · Пропущу»). */
  series?: boolean;
  /** На этот день стоит «Не бегаю». */
  rest: boolean;
};

export type NextPlanResponse = {
  /** Ближайший будущий план — на любую дату: разовый или вхождение постоянного. */
  plan: PlanItem | null;
  day: NextStartDay;
  /** Постоянный план, в том числе на паузе («Продолжить»). */
  series?: PlanSeries | null;
  series_hint?: SeriesHint | null;
};

/** Локация публичного плана в профиле; cancelled — старт там отменён, regular — план постоянный («↻»). */
export type ProfilePlanPlace = { name: string; slug: string; cancelled: boolean; regular?: boolean };

/**
 * Строка в шапке профиля: ближайший публичный план — на любую дату. null —
 * публичных планов впереди нет (или профиль зрителю не виден).
 */
export type ProfilePlan = {
  date: string; // YYYY-MM-DD
  kind: "regular" | "special";
  /** Название особой даты; у суббот — null. */
  title: string | null;
  /** Локации дня: на особую дату их бывает до трёх. */
  places: ProfilePlanPlace[];
  /** Сколько дат с публичными планами впереди, эта тоже. */
  upcoming_count: number;
  /** Ответ хозяину про план, которого другие не видят. */
  only_me: boolean;
};

/** День календаря «Планы» в профиле: публичные планы или публичная отметка «Не бегаю» (places пустой). */
export type ProfileCalendarDay = {
  date: string; // YYYY-MM-DD
  kind: "regular" | "special";
  title: string | null;
  places: ProfilePlanPlace[];
  rest: boolean;
};

/**
 * Вкладка «Планы» профиля на месяц — только дни с сегодняшнего: прошлое
 * («планировал и не пришёл») чужим не показываем. Ребёнку календарь пустой.
 */
export type ProfileCalendar = {
  month: string; // YYYY-MM
  today: string; // YYYY-MM-DD
  /** Смотрит хозяин — видит то же, что другие, плюс ссылку на «Мои планы». */
  owner: boolean;
  days: ProfileCalendarDay[];
};

/**
 * С чего начинается переключатель «Публично | Приватно» в окне плана.
 * default_public — как профиль: открыт — публично. forced: profile_private —
 * профиль закрыт, публичный план невозможен; minor — по протоколам младше 15,
 * имя не показываем, публичный план — число «ребёнок».
 */
export type PlannerSettings = {
  default_public: boolean;
  forced: "profile_private" | "minor" | null;
};

/**
 * День оргкалендаря. people/private/children — только у будущих дней (как у
 * LocationPlansDate); у прошедших пусто и нули — там came.
 */
export type OrganizerCalendarDay = PlanAudience & {
  date: string;
  kind: "regular" | "special";
  title: string | null;
  /** Номер прошедшего старта (из протокола). */
  number: number | null;
  number_forecast: number | null;
  past: boolean;
  /** Всего планирующих: разовые и постоянные. */
  planned: number;
  /**
   * Только прошедшие: сколько из планировавших разово были на старте (любое
   * присутствие в протоколе); null — протокола нет.
   */
  came: number | null;
  /**
   * Постоянные: у будущих — как разовые, у прошедших — только total
   * (вхождения на дату, без пауз), имён нет.
   */
  regulars?: PlanAudienceGroup;
  /** Только прошедшие: сколько постоянных были на старте; null — протокола нет. */
  regulars_came?: number | null;
};

export type OrganizerCalendar = {
  location: { slug: string; name: string };
  month: string; // YYYY-MM
  days: OrganizerCalendarDay[];
};

/** Домашняя локация человека — как её определяет сайт (выбранная руками или самая частая). */
export type GuestHome = {
  name: string;
  slug: string;
  city: string | null;
  /** Дом — эта самая локация (сервер сравнивает ключи идентичности: слаги у дома и страницы бывают разные). */
  here: boolean;
};

/** Строка «Кто у нас бежит»: человек с публичным планом на эту локацию. */
export type GuestRow = {
  name: string;
  /** Хендл профиля — ведёт на страницу участника в оргкабинете; null — без ссылки. */
  handle: string | null;
  /** Всего пробежек, как в профиле; null — посчитать не удалось. */
  runs: number | null;
  /** Пробежек на этой локации; null — профиль в системах не привязан, как у runs. */
  runs_here: number | null;
  /** Ни пробежки, ни волонтёрства здесь до даты плана. */
  first_here: boolean;
  /**
   * Знаковый старт этой даты — столбец «Знаковый старт». Номерные (юбилеи,
   * первая пробежка) — только на три ближайших старта локации, «впервые у
   * нас» — на любую дату.
   */
  marks: PlanMark[];
  home: GuestHome | null;
  /** Что даст этот старт по челленджам — столбец «Челленджи»; только три ближайших старта. */
  gains?: GuestGain[];
};

/**
 * Строка столбца «Челленджи»: пара слов («новая локация (31-я)»,
 * «Нумератор ПРО: 21 → 22», «🥈 Клуб Нельсона») и полный текст подсказки —
 * у номерных челленджей по строке на челлендж.
 */
export type GuestGain = { label: string; hint: string };

/**
 * Постоянные на дату: кто (handles — строки regulars_people ответа, в их
 * порядке), сколько, приватные и дети; метки знаковых стартов — только у дат
 * горизонта юбилеев (трёх ближайших стартов).
 */
export type GuestRegulars = {
  total: number;
  private: number;
  children: number;
  handles: string[];
  marks: Record<string, PlanMark[]>;
  /** Челленджи постоянных на эту дату — по хендлу, как marks. */
  gains?: Record<string, GuestGain[]>;
};

/**
 * Дата с планами: публичные — строками, приватные и дети — числом, как в
 * блоке локации. people / private / children — разовые, regulars — постоянные,
 * total — все вместе.
 */
export type OrganizerGuestsDate = {
  date: string;
  kind: "regular" | "special";
  title: string | null;
  total: number;
  private: number;
  children: number;
  people: GuestRow[];
  regulars?: GuestRegulars;
};

export type OrganizerGuests = {
  location: { slug: string; name: string };
  weeks: number;
  /** Только будущие даты (с сегодняшней по Москве), на которые есть планы. */
  dates: OrganizerGuestsDate[];
  /** Постоянные этой локации — один раз на весь ответ, без меток. */
  regulars_people?: GuestRow[];
};

/** Страница участника в оргкабинете: что важно знать организатору о человеке, который к ним едет. */
export type OrganizerGuestCard = {
  location: { slug: string; name: string };
  person: { name: string; handle: string };
  /** Будущие публичные планы человека на эту локацию: разовые и три ближайших субботы постоянного. */
  planned_dates: { date: string; kind: "regular" | "special"; title: string | null }[];
  /** Постоянный план сюда: «↻ Каждую субботу с 31 октября». */
  series?: { starts_on: string; until: string | null } | null;
  stats: {
    runs: number | null;
    volunteering: number | null;
    locations: number | null;
    /** null — профиль в системах не привязан: «0 здесь» было бы неправдой. */
    runs_here: number | null;
    volunteering_here: number | null;
    last_here: string | null; // YYYY-MM-DD
    best_here_sec: number | null;
    first_here: boolean;
  };
  home: GuestHome | null;
  /** «Будет 100-я пробежка», «10-я пробежка здесь». */
  jubilees: { label: string }[];
  /**
   * Пояснение под юбилеями: план есть, но дальше трёх ближайших стартов —
   * «Юбилеи считаем на три ближайших старта». null — пояснять нечего.
   */
  jubilees_note?: string | null;
  /**
   * Дата ближайшего визита, что ещё впереди: сегодняшний, уже лёгший в протокол,
   * и отменённый старт — не впереди. null — таких визитов нет.
   */
  challenges_date: string | null;
  /**
   * Что даст этот визит по челленджам: «Новая локация» — «Станет 58-й
   * локацией», «Нумератор ПРО: ≈№221 — новый номер» — «14 → 15 номеров,
   * до золота 15», «Клуб Нельсона — 🥈 серебро» — «≈№222 — новый нельсон, …».
   */
  challenges: GuestChallenge[];
  /**
   * Визит дальше трёх ближайших стартов: «Номерные челленджи считаем на три
   * ближайших старта». null — пояснять нечего.
   */
  challenges_note?: string | null;
  /**
   * Следующие даты сюда в горизонте юбилеев — то же, что их строки в
   * столбце «Челленджи»; даты, где визит ничего не даст, не приходят.
   */
  later_visits?: { date: string; challenges: GuestChallenge[] }[];
};

/** Строка «что даст визит» на странице участника: жирное название и пояснение. */
export type GuestChallenge = { title: string; text: string };

export type SpecialStartDate = {
  id: string;
  start_date: string;
  title: string;
  platforms: string[];
};

export type SpecialStartDateDraft = Omit<SpecialStartDate, "id">;

/** Особая дата для календаря «Планы» — публичная, без id и без привязки к локации. */
export type SpecialDateInfo = {
  date: string; // YYYY-MM-DD
  title: string;
  /** Коды систем, у локаций которых в этот день может быть старт. */
  platforms: string[];
};

/**
 * Локация в окне плана «от даты» (кабинет): на неё в эту дату можно
 * запланировать — правила те же, что у календаря локации.
 */
export type PlannerLocationOption = {
  /** Ключ идентичности — как identityKey окна от локации. */
  key: string;
  slug: string;
  name: string;
  city: string | null;
  /** Системы, у которых в эту дату здесь старт. */
  platforms: string[];
  number_forecast: number | null;
  /** Старт отменён: выбрать можно только свой план (чтобы убрать). */
  cancelled: boolean;
  /** У зрителя уже план сюда на эту дату — выбор откроет его правку. */
  mine: boolean;
  /** Локация постоянного плана зрителя. */
  regular: boolean;
  /** «Скоро открытие»: старт ещё не проводился. */
  upcoming?: boolean;
};

export type PlannerLocations = {
  date: string;
  /** Что за день: суббота или особая дата. Дня без стартов сервер не примет (400). */
  kind: "regular" | "special";
  title: string | null;
  /** Сколько планов можно на этот день: 1, на особую дату — 3. */
  limit: number;
  /** На этот день у человека «Не бегаю» — план её снимет. */
  rest: boolean;
  /**
   * Разовые планы человека на этот день — их считает limit (суббота
   * постоянного не в счёт). Набралось limit — другую локацию не выбрать.
   */
  planned?: PlanLocation[];
  /** Без запроса: постоянная, домашняя, недавние планы, самые частые (до 8). */
  suggested: PlannerLocationOption[];
  /** Поиск по названию, городу и региону (запрос от двух букв, до 20). */
  results: PlannerLocationOption[];
  /** Нашлось больше, чем в results: окно просит уточнить запрос. */
  truncated?: boolean;
};

const enc = encodeURIComponent;

/** Куда можно запланировать в дату: подсказки и поиск для окна «от даты». */
export function getPlannerLocations(date: string, query: string, init?: { signal?: AbortSignal }) {
  const q = query.trim();
  const tail = q ? `&q=${enc(q)}` : "";
  return apiFetch<PlannerLocations>(`/planner/locations?date=${enc(date)}${tail}`, init);
}

export function getLocationPlans(identityKey: string) {
  return apiFetch<LocationPlansBlock>(`/planner/location?key=${enc(identityKey)}`);
}

export function getLocationStartDates(identityKey: string) {
  return apiFetch<LocationStartDates>(`/planner/location/dates?key=${enc(identityKey)}`);
}

export function getMyPlans(from: string, to: string) {
  return apiFetch<MyPlans>(`/planner/my?from=${enc(from)}&to=${enc(to)}`);
}

/** Особые даты окна [from, to] — подсветить в календаре и до того, как на них есть план. */
export function getSpecialDates(from: string, to: string) {
  return apiFetch<{ items: SpecialDateInfo[] }>(`/planner/special-dates?from=${enc(from)}&to=${enc(to)}`);
}

/** Ближайший публичный план в профиле человека — по хендлу из адреса (номер или ник). */
export function getProfilePlan(handle: string) {
  return apiFetch<ProfilePlan | null>(`/planner/profile?handle=${enc(handle)}`);
}

/**
 * Активна ли вкладка «Планы» профиля: public — есть будущие публичные планы
 * или публичное «Не бегаю» (хозяину — всегда), empty — нет, closed — профиль закрыт.
 */
export type ProfilePlansTab = { state: "public" | "empty" | "closed" };

export function getProfilePlansTab(handle: string) {
  return apiFetch<ProfilePlansTab>(`/planner/profile/tab?handle=${enc(handle)}`);
}

/** Календарь публичных планов профиля на месяц; закрытый профиль — 404. */
export function getProfileCalendar(handle: string, month: string) {
  return apiFetch<ProfileCalendar>(`/planner/profile/calendar?handle=${enc(handle)}&month=${enc(month)}`);
}

export function getNextPlan() {
  return apiFetch<NextPlanResponse>("/planner/next");
}

/**
 * Поставить или поправить план (ключ — локация и дата). is_public — выбор
 * переключателя окна; без поля сервер берёт прежний выбор или профиль.
 * keep_series — на особую дату постоянный план этого дня не вытеснять, а
 * закрепить разовым («+ ещё локация» 1 января); на обычную дату не действует.
 * replace_key — день заполнен: план этого дня на этой локации сервер уберёт
 * в той же транзакции («План … будет отменён»).
 */
export function savePlan(body: {
  key: string;
  plan_date: string;
  is_public?: boolean;
  keep_series?: boolean;
  replace_key?: string;
}) {
  return apiFetch<PlanSaveResult>("/planner/plans", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/**
 * Убрать план. rest — и отметить «Не бегаю» на его дату одним запросом
 * (поездку убрали, а в постоянную локацию в этот день тоже не идут); на дате
 * есть ещё планы — 409 с готовым вопросом, и ничего не удалено.
 */
export function deletePlan(planId: string, options?: { rest?: boolean }) {
  const query = options?.rest ? "?rest=true" : "";
  return apiFetch<void>(`/planner/plans/${enc(planId)}${query}`, { method: "DELETE" });
}

/** Постоянный план и подсказка «9 из 10» (подсказки нет, пока план покрывает будущее). */
export function getMySeries() {
  return apiFetch<MySeries>("/planner/series");
}

/**
 * «Планирую каждую субботу» и правка постоянного плана. Постоянный план на
 * другой локации — 409: текст ошибки — готовый вопрос «Заменить?», повтор с
 * replace=true заменяет его.
 */
export function saveSeries(body: SeriesSaveBody) {
  return apiFetch<SeriesSaveResult>("/planner/series", { method: "PUT", body: JSON.stringify(body) });
}

/** «Остановить»: будущие субботы уходят, прошлые остаются в истории. */
export function stopSeries() {
  return apiFetch<void>("/planner/series", { method: "DELETE" });
}

/** «Продолжить» план на паузе. */
export function resumeSeries() {
  return apiFetch<{ series: PlanSeries }>("/planner/series/resume", { method: "POST" });
}

/** Что встанет в постоянный план: ближайшие даты с состоянием и сколько стартов в сроке. */
export function getSeriesPreview(params: { key: string; starts_on?: string | null; until?: string | null; limit?: number }) {
  const query = [`key=${enc(params.key)}`];
  if (params.starts_on) query.push(`starts_on=${enc(params.starts_on)}`);
  if (params.until) query.push(`until=${enc(params.until)}`);
  if (params.limit) query.push(`limit=${enc(String(params.limit))}`);
  return apiFetch<SeriesPreview>(`/planner/series/preview?${query.join("&")}`);
}

/** «Не сейчас» у подсказки «9 из 10» — 30 дней на всех устройствах. */
export function snoozeSeriesHint() {
  return apiFetch<void>("/planner/series/hint/snooze", { method: "POST" });
}

/**
 * «Не бегаю» на день старта (суббота или особая дата). На день с планами
 * пробежки сервер отвечает 409 — текст ошибки уже готовый вопрос «Убрать план
 * и отметить «Не бегаю»?»; с replace=true планы этого дня убираются и
 * ставится отметка. isPublic — видна ли отметка в
 * профиле; без него у новой отметки — как у профиля, у прежней — как была.
 */
export function saveDayRest(planDate: string, replace = false, isPublic?: boolean) {
  const body: { replace: boolean; is_public?: boolean } = { replace };
  if (isPublic !== undefined) {
    body.is_public = isPublic;
  }
  return apiFetch<{ plan: PlanItem }>(`/planner/rest/${enc(planDate)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

/** Снять «Не бегаю»: строка дня исчезает. */
export function clearDayRest(planDate: string) {
  return apiFetch<void>(`/planner/rest/${enc(planDate)}`, { method: "DELETE" });
}

/** С чего начинать переключатель «Публично | Приватно» и можно ли его трогать. */
export function getPlannerSettings() {
  return apiFetch<PlannerSettings>("/settings/planner");
}

export function getOrganizerCalendar(slug: string, month: string) {
  return apiFetch<OrganizerCalendar>(`/organizer/${enc(slug)}/calendar?month=${enc(month)}`);
}

export function getOrganizerGuests(slug: string, weeks: number) {
  return apiFetch<OrganizerGuests>(`/organizer/${enc(slug)}/guests?weeks=${enc(String(weeks))}`);
}

export function getOrganizerGuest(slug: string, handle: string) {
  return apiFetch<OrganizerGuestCard>(`/organizer/${enc(slug)}/guests/${enc(handle)}`);
}

export function listSpecialStartDates() {
  return apiFetch<{ items: SpecialStartDate[] }>("/admin/start-dates");
}

export function createSpecialStartDate(body: SpecialStartDateDraft) {
  return apiFetch<SpecialStartDate>("/admin/start-dates", { method: "POST", body: JSON.stringify(body) });
}

export function updateSpecialStartDate(id: string, body: SpecialStartDateDraft) {
  return apiFetch<SpecialStartDate>(`/admin/start-dates/${enc(id)}`, { method: "PUT", body: JSON.stringify(body) });
}

export function deleteSpecialStartDate(id: string) {
  return apiFetch<void>(`/admin/start-dates/${enc(id)}`, { method: "DELETE" });
}
