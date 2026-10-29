// Адреса планировщика, которые нужны лёгким местам («Обзор», шапка профиля,
// настройки): тянуть ради них «Мои планы» или окно плана незачем.

/**
 * «Уведомления о планах» — в «Моих планах», а не в общих настройках (решения
 * Дмитрия 26–27.10.2026): на них ведут «Выключить» в самом напоминании,
 * ссылки сообщения о паузе постоянного плана и настройки сайта. Кто видит
 * план, выбирают в окне самого плана.
 */
export const PLAN_NOTIFICATIONS_ANCHOR = "plan-notifications";
export const PLAN_NOTIFICATIONS_HREF = `/plans#${PLAN_NOTIFICATIONS_ANCHOR}`;

/** В общих настройках от раздела остался якорь со ссылкой сюда: /settings#planner. */
export const PLANNER_SETTINGS_ANCHOR = "planner";

/**
 * Привязка профилей систем — «Профили беговых систем» на «Обзоре» (тот же
 * адрес, что в сообщении о паузе постоянного плана): без привязки сайт не
 * видит пробежек, и протокол план не будит.
 */
export const PROFILE_LINKS_HREF = "/dashboard#profiles";

// Окно плана от локации на конкретный день: /locations/{slug}#plan=YYYY-MM-DD.
// Якорь, а не ?plan=: адрес локации для роботов и кэшей остаётся один.
const PLAN_HASH_RE = /^#plan=(\d{4}-\d{2}-\d{2})$/;

/** Дата из якоря #plan=YYYY-MM-DD; нет якоря — null. */
export function planDateFromHash(hash: string): string | null {
  return PLAN_HASH_RE.exec(hash)?.[1] ?? null;
}

// Разовые команды «Планов» в якоре — из сообщений «Ещё ходите?» и
// напоминания: открыть карточку постоянного плана с готовым подтверждением или
// день с «Пропустить». Страница выполняет их и снимает якорь (replaceState),
// чтобы обновление и «Назад» не повторяли действие.
export const SERIES_ANCHOR = "series";
export type PlansHashCommand =
  | { kind: "series" }
  | { kind: "series-resume" }
  | { kind: "series-stop" }
  | { kind: "skip"; date: string };

const SKIP_HASH_RE = /^#skip=(\d{4}-\d{2}-\d{2})$/;

/** Команда из якоря «Планов»; нет команды — null. */
export function plansHashCommand(hash: string): PlansHashCommand | null {
  if (hash === `#${SERIES_ANCHOR}`) return { kind: "series" };
  if (hash === "#series-resume") return { kind: "series-resume" };
  if (hash === "#series-stop") return { kind: "series-stop" };
  const skip = SKIP_HASH_RE.exec(hash);
  return skip ? { kind: "skip", date: skip[1] } : null;
}
