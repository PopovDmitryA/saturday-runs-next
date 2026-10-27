// API рейтингов челленджей (/ratings/p-index и соседи). Свой модуль, как у
// рейтинга быстрых: строка здесь — значение челленджа с датой достижения, и
// форма LeaderboardRow (счётчики по системам с дельтой) ей не подходит.
// Таблицы считаются фоном на сервере и лежат готовыми снимками — запрос
// ничего не пересчитывает.

export type ChallengeRatingRow = {
  rank: number;
  /** Сдвиг места за неделю; null — неделю назад значения не было. */
  rank_delta: number | null;
  row_key: string;
  display_name: string | null;
  site_serial_id: number | null;
  value: number;
  value_delta: number;
  /** Когда впервые набрано текущее значение (YYYY-MM-DD). */
  reached_on: string;
  /** p- и V-индекс: сколько не хватает до индекса на единицу больше. */
  to_next?: number | null;
  /** «60 секунд» — секунды числом, «Круглый год» — даты «MM-DD», ближайшие первыми. */
  missing?: (number | string)[] | null;
  missing_total?: number | null;
  /** Уилсон: от какого до какого номера старта закрыта цепочка. */
  chain_from?: number | null;
  chain_to?: number | null;
  /** Серия: лучшая серия ещё идёт. */
  ongoing?: boolean | null;
  /** «Ударный год»: пробежек в текущем году. */
  this_year?: number | null;
  /** Последний выход на старт в этом срезе (YYYY-MM-DD). */
  last_seen?: string | null;
  /** Значение в каждой системе — только в общем зачёте. */
  platforms?: Record<string, number> | null;
};

/** Шаг к индексу +1: location=null — новые локации (count штук по add). */
export type ToNextStep = { location: string | null; add: number; count?: number | null };

export type MyChallengeRatingRow = ChallengeRatingRow & {
  included: boolean;
  /** p- и V-индекс: где добрать до индекса +1. */
  to_next_plan?: ToNextStep[] | null;
};

export type ChallengeVariantOption = {
  key: string;
  label: string;
  platform_options: string[];
};

export type ChallengeMissingKind = "seconds" | "calendar_days" | "alphabet";

export type ChallengeRatingResponse = {
  code: string;
  slug: string;
  title: string;
  icon: string;
  description: string;
  ranking_note: string;
  /** Пояснение к срезу (архив parkrun). */
  scope_note: string | null;
  /** Показывать ли недельные сдвиги. */
  show_deltas: boolean;
  value_label: string;
  unit_forms: [string, string, string] | null;
  /** Склонение колонки «До следующего»; null — колонки нет. */
  to_next_forms: [string, string, string] | null;
  reached_format: "date" | "year";
  missing_kind: ChallengeMissingKind | null;
  /** Колонка «Старты» (№от–№до цепочки) — у Уилсона. */
  shows_chain: boolean;
  variant: string;
  variant_label: string | null;
  /** Пусто — переключателя вариантов нет. */
  variants: ChallengeVariantOption[];
  platform: string;
  platform_options: string[];
  platform_labels: Record<string, string>;
  /** all — все участники, active — выходившие на старт за active_days дней. */
  activity: ChallengeActivity;
  active_days: number;
  refresh_hours: number;
  limit: number;
  rows: ChallengeRatingRow[];
  entrants: number;
  latest_event_date: string | null;
  built_at: string | null;
  ready: boolean;
};

export type MyChallengeRatingResponse = {
  code: string;
  variant: string;
  platform: string;
  activity: ChallengeActivity;
  row: MyChallengeRatingRow | null;
  included: boolean;
};

export type ChallengeHubCard = {
  code: string;
  slug: string;
  title: string;
  icon: string;
  value_label: string;
  unit_forms: [string, string, string] | null;
  platform: string;
  rows: ChallengeRatingRow[];
  me: MyChallengeRatingRow | null;
};

export type ChallengeHubResponse = {
  platform: string;
  cards: ChallengeHubCard[];
};

async function challengeFetch<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/challenge-ratings${path}`, {
    credentials: "include",
    headers: { Accept: "application/json" },
    signal,
  });
  if (response.status === 401) {
    throw new Error("unauthorized");
  }
  if (!response.ok) {
    throw new Error(`Не удалось загрузить рейтинг (${response.status})`);
  }
  return (await response.json()) as T;
}

export type ChallengeActivity = "all" | "active";

/** Срез таблицы: система, вариант счёта (пустая строка — первый) и активность. */
export type ChallengeSlice = { platform: string; variant: string; activity: ChallengeActivity };

function sliceQuery(slice: ChallengeSlice): string {
  const params = new URLSearchParams();
  if (slice.platform !== "all") {
    params.set("platform", slice.platform);
  }
  if (slice.variant) {
    params.set("variant", slice.variant);
  }
  if (slice.activity !== "all") {
    params.set("activity", slice.activity);
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

function platformQuery(platform: string): string {
  return sliceQuery({ platform, variant: "", activity: "all" });
}

export function getChallengeRating(slug: string, slice: ChallengeSlice, signal?: AbortSignal) {
  return challengeFetch<ChallengeRatingResponse>(`/${slug}${sliceQuery(slice)}`, signal);
}

export function getMyChallengeRow(slug: string, slice: ChallengeSlice, signal?: AbortSignal) {
  return challengeFetch<MyChallengeRatingResponse>(`/${slug}/me${sliceQuery(slice)}`, signal);
}

export function getChallengeHub(platform: string, signal?: AbortSignal) {
  return challengeFetch<ChallengeHubResponse>(`/hub${platformQuery(platform)}`, signal);
}

/** Место человека в рейтинге челленджа — для ссылки с карточки «Моих достижений». */
export type ChallengePlace = {
  slug: string;
  variant: string | null;
  platform: string;
  rank: number | null;
  entrants: number | null;
};

export type MyChallengePlacesResponse = {
  platform: string;
  /** Ключ — код карточки челленджа. */
  places: Record<string, ChallengePlace>;
};

export function getMyChallengePlaces(platform: string, signal?: AbortSignal) {
  return challengeFetch<MyChallengePlacesResponse>(`/my-places${platformQuery(platform)}`, signal);
}

/** Адрес рейтинга с тем же вариантом и системой, что у карточки. */
export function challengePlaceHref(place: ChallengePlace): string {
  const params = new URLSearchParams();
  if (place.platform !== "all") {
    params.set("platform", place.platform);
  }
  if (place.variant) {
    params.set("variant", place.variant);
  }
  const query = params.toString();
  return `/ratings/${place.slug}${query ? `?${query}` : ""}`;
}
