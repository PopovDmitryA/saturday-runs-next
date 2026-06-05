export type ShareFormatId = "square" | "story" | "wide";

export type SharePeriodId = "all_time" | "year" | "last_run";

export const SHARE_PERIODS: { id: SharePeriodId; title: string; description: string }[] = [
  {
    id: "all_time",
    title: "За всё время",
    description: "Вся история пробежек и волонтёрства",
  },
  {
    id: "year",
    title: "За текущий год",
    description: "С 1 января текущего года",
  },
  {
    id: "last_run",
    title: "Последняя пробежка",
    description: "Локация, время и место на последнем старте",
  },
];

export type ShareFormat = {
  id: ShareFormatId;
  title: string;
  hint: string;
  width: number;
  height: number;
  maxFields: number;
};

export const SHARE_FORMATS: ShareFormat[] = [
  {
    id: "square",
    title: "Квадрат 1:1",
    hint: "VK, лента Instagram · 1080×1080",
    width: 1080,
    height: 1080,
    maxFields: 3,
  },
  {
    id: "story",
    title: "Stories",
    hint: "Вертикаль · 1080×1920",
    width: 1080,
    height: 1920,
    maxFields: 3,
  },
  {
    id: "wide",
    title: "Широкая",
    hint: "Telegram, превью ссылки · 1200×630",
    width: 1200,
    height: 630,
    maxFields: 3,
  },
];

export type ShareFieldId =
  | "total_runs"
  | "total_volunteering"
  | "unique_locations"
  | "unique_run_locations"
  | "total_distance_km"
  | "saturday_streak"
  | "saturday_consistency"
  | "pr_count"
  | "best_finish_time"
  | "runs_current_year"
  | "runs_last_12_months"
  | "volunteering_index"
  | "top_location"
  | "next_run_club"
  | "top_volunteer_role";

export type ShareFieldDef = {
  id: ShareFieldId;
  label: string;
  hint: string;
};

export const SHARE_FIELDS: ShareFieldDef[] = [
  { id: "total_runs", label: "Всего пробежек", hint: "Сумма по всем системам" },
  { id: "total_volunteering", label: "Волонтёрств", hint: "Смены и роли" },
  { id: "unique_locations", label: "Уникальных локаций", hint: "Пробежки и волонтёрство" },
  { id: "unique_run_locations", label: "Локаций с пробежками", hint: "География стартов" },
  { id: "total_distance_km", label: "Дистанция", hint: "Примерно, 5 км × пробежки" },
  { id: "saturday_streak", label: "Серия суббот", hint: "Подряд с активностью" },
  { id: "saturday_consistency", label: "Субботы за год", hint: "Доля суббот с пробежкой/волонтёрством" },
  { id: "pr_count", label: "Личных рекордов", hint: "PR по финишному времени" },
  { id: "best_finish_time", label: "Лучшее время", hint: "Лучший результат на дистанции" },
  { id: "runs_current_year", label: "Пробежек в этом году", hint: "С 1 января" },
  { id: "runs_last_12_months", label: "Пробежек за 12 мес.", hint: "Скользящий год" },
  { id: "volunteering_index", label: "Индекс волонтёрства", hint: "Баланс пробежек и помощи" },
  { id: "top_location", label: "Любимая локация", hint: "Где чаще всего бегали" },
  { id: "top_volunteer_role", label: "Частая роль", hint: "На волонтёрстве" },
  { id: "next_run_club", label: "До клуба", hint: "Следующий рубеж по пробежкам" },
];

export type SharePresetId =
  | "tourist"
  | "runner"
  | "volunteer"
  | "saturday"
  | "balanced"
  | "custom";

export type SharePreset = {
  id: SharePresetId;
  title: string;
  description: string;
  fields: ShareFieldId[];
  /** Rendered in the center zone of the card. */
  primaryFieldId?: ShareFieldId;
};

export const SHARE_PRESETS: SharePreset[] = [
  {
    id: "tourist",
    title: "Турист",
    description: "Много локаций и любимые парки",
    fields: ["unique_locations", "total_runs"],
    primaryFieldId: "unique_locations",
  },
  {
    id: "runner",
    title: "Бегун",
    description: "Объём, темп и рекорды",
    fields: ["total_runs", "total_distance_km", "best_finish_time"],
    primaryFieldId: "total_distance_km",
  },
  {
    id: "volunteer",
    title: "Волонтёр",
    description: "Помощь на стартах",
    fields: ["total_volunteering", "volunteering_index", "top_volunteer_role"],
    primaryFieldId: "volunteering_index",
  },
  {
    id: "saturday",
    title: "Субботник",
    description: "Регулярность парковых стартов",
    fields: ["saturday_streak", "saturday_consistency", "total_runs"],
    primaryFieldId: "saturday_consistency",
  },
  {
    id: "balanced",
    title: "Сводка",
    description: "Универсальный набор",
    fields: ["total_runs", "total_volunteering", "unique_locations"],
    primaryFieldId: "total_volunteering",
  },
  {
    id: "custom",
    title: "Собрать самому",
    description: "Выберите поля вручную",
    fields: [],
  },
];

export const SHARE_WATERMARK = "run5k.run";
export const SHARE_PROJECT_NAME = "Статистика парковых пробежек";
export const SHARE_TAGLINE = "5 вёрст · S95 · parkrun — в одном месте";

export type ShareTextColor = "white" | "black" | "cream" | "gold" | "mint" | "sky" | "coral";

export type ShareTextTreatment = "light" | "dark";

export type ShareTextColorDef = {
  id: ShareTextColor;
  label: string;
  swatch: string;
  primary: string;
  secondary: string;
  divider: string;
  treatment: ShareTextTreatment;
};

export const SHARE_TEXT_COLORS: ShareTextColorDef[] = [
  {
    id: "white",
    label: "Белый",
    swatch: "#ffffff",
    primary: "#ffffff",
    secondary: "rgba(255, 255, 255, 0.88)",
    divider: "rgba(255, 255, 255, 0.14)",
    treatment: "light",
  },
  {
    id: "black",
    label: "Чёрный",
    swatch: "#0f172a",
    primary: "#0f172a",
    secondary: "rgba(15, 23, 42, 0.78)",
    divider: "rgba(15, 23, 42, 0.16)",
    treatment: "dark",
  },
  {
    id: "cream",
    label: "Кремовый",
    swatch: "#fef3c7",
    primary: "#fffbeb",
    secondary: "rgba(255, 251, 235, 0.9)",
    divider: "rgba(255, 251, 235, 0.2)",
    treatment: "light",
  },
  {
    id: "gold",
    label: "Золотой",
    swatch: "#fbbf24",
    primary: "#fde68a",
    secondary: "rgba(253, 230, 138, 0.92)",
    divider: "rgba(253, 230, 138, 0.22)",
    treatment: "light",
  },
  {
    id: "mint",
    label: "Мятный",
    swatch: "#34d399",
    primary: "#a7f3d0",
    secondary: "rgba(167, 243, 208, 0.92)",
    divider: "rgba(167, 243, 208, 0.22)",
    treatment: "light",
  },
  {
    id: "sky",
    label: "Голубой",
    swatch: "#38bdf8",
    primary: "#bae6fd",
    secondary: "rgba(186, 230, 253, 0.92)",
    divider: "rgba(186, 230, 253, 0.22)",
    treatment: "light",
  },
  {
    id: "coral",
    label: "Коралловый",
    swatch: "#fb7185",
    primary: "#fecdd3",
    secondary: "rgba(254, 205, 211, 0.92)",
    divider: "rgba(254, 205, 211, 0.22)",
    treatment: "light",
  },
];

export function getShareTextColorDef(id: ShareTextColor): ShareTextColorDef {
  return SHARE_TEXT_COLORS.find((item) => item.id === id) ?? SHARE_TEXT_COLORS[0];
}

export function getShareFormat(id: ShareFormatId): ShareFormat {
  return SHARE_FORMATS.find((item) => item.id === id) ?? SHARE_FORMATS[0];
}

export function trimFieldsForFormat(fields: ShareFieldId[], format: ShareFormat): ShareFieldId[] {
  return fields.slice(0, format.maxFields);
}
