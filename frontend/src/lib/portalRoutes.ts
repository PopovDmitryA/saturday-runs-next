/**
 * Адреса портального редизайна. Пока живут под префиксом /new (тёмный запуск —
 * старые страницы на "/", "/about", "/login" не тронуты и не знают об этом коде).
 *
 * При релизе редизайна — единственный файл, который нужно поправить: замените
 * значения на канонические пути ("/", "/about", "/login") и уберите /new/*
 * записи из STATIC_ROUTES в App.tsx. Все ссылки в features/portal/* уже берут
 * адреса отсюда, повторно искать по коду не нужно.
 */
export const PORTAL_HOME_HREF = "/new";
export const PORTAL_ABOUT_HREF = "/new/about";
export const PORTAL_ABOUT_PRIVACY_HREF = `${PORTAL_ABOUT_HREF}#privacy`;
export const PORTAL_LOGIN_HREF = "/new/login";

/**
 * Личный кабинет в портальном дизайне (тёмный запуск). При релизе значения
 * меняются на канонические ("/dashboard", "/runs", …) — старые страницы
 * уходят вместе с /new-записями из STATIC_ROUTES.
 */
export const PORTAL_CABINET_HREF = "/new/dashboard";
export const PORTAL_CABINET_RUNS_HREF = "/new/runs";
export const PORTAL_CABINET_VOLUNTEERING_HREF = "/new/volunteering";
export const PORTAL_CABINET_ACHIEVEMENTS_HREF = "/new/achievements";
export const PORTAL_CABINET_MEETINGS_HREF = "/new/co-runners";
export const PORTAL_CABINET_MAP_HREF = "/new/maps";
export const PORTAL_CABINET_HISTORY_HREF = "/new/history";
