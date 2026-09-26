/**
 * Названия рейтингов — из одного места, дерева навигации (RATING_GROUPS в
 * portal/nav/siteNav.ts).
 *
 * Раньше у одного рейтинга было до четырёх имён: одно в меню, другое на
 * карточке хаба, третье в хлебных крошках, четвёртое в заголовке с сервера
 * («Первые места» / «Количество первых мест» / «Рейтинг количества первых
 * мест»), а на хабе две разные карточки назывались «Уникальные локации».
 * Человек нажимал в меню одно, открывалось другое, и он сомневался, туда ли
 * попал (ревью навигации 25.09.2026). Теперь хаб, крошки и заголовки страниц
 * берут слова отсюда, а переименование делается в дереве — и сразу везде.
 */
import { RATING_GROUPS } from "../portal/nav/siteNav";
import type { LeaderboardMetric } from "./leaderboardsApi";

/** Ключ рейтинга в дереве: сегмент адреса /ratings/{key}. */
export type RatingKey = string;

export type RatingName = {
  /** Название рейтинга — как в меню. */
  label: string;
  /** Название группы — «Бегуны», «Волонтёры», «Туристы», «Локации». */
  group: string;
};

const BY_KEY = new Map<RatingKey, RatingName>();
for (const group of RATING_GROUPS) {
  for (const item of group.items) {
    BY_KEY.set(item.key, { label: item.label, group: group.title });
  }
}

/**
 * Имя рейтинга по ключу дерева. Рейтинга в дереве нет (закрытые «Трассы»
 * живут там только у админа) — берём запасное имя от вызывающего.
 */
export function ratingName(key: RatingKey, fallback?: RatingName): RatingName {
  return BY_KEY.get(key) ?? fallback ?? { label: key, group: "" };
}

/** Заголовок группы на хабе — тот же, что у группы в меню. */
export function ratingGroupTitle(groupKey: string): string {
  return RATING_GROUPS.find((group) => group.key === groupKey)?.title ?? groupKey;
}

/**
 * Метрика лидерборда → ключ дерева. В API метрики пишутся через подчёркивание
 * (win_locations), в адресах и в дереве — через дефис (win-locations).
 */
export function metricRatingKey(metric: LeaderboardMetric): RatingKey {
  return metric.replace(/_/g, "-");
}
