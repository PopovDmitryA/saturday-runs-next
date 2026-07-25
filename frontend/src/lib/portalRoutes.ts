/**
 * Канонические адреса портального редизайна. С релиза (июль 2026) портал живёт
 * на основных путях сайта: "/", "/about", "/login"; адреса тёмного запуска
 * (/new/*) удалены — наружу они не публиковались. Все ссылки в
 * features/portal/* берут адреса отсюда.
 */
export const PORTAL_HOME_HREF = "/";
export const PORTAL_ABOUT_HREF = "/about";
export const PORTAL_ABOUT_PRIVACY_HREF = `${PORTAL_ABOUT_HREF}#privacy`;
export const PORTAL_LOGIN_HREF = "/login";
export const PORTAL_BLOG_HREF = "/blog";

/**
 * Личный кабинет в портальном дизайне — пока тёмный запуск под /new/*, рядом
 * со старым кабинетом на канонических адресах ("/dashboard", "/runs", …).
 * При релизе значения меняются на канонические, а старые страницы уходят
 * вместе с /new-записями из STATIC_ROUTES в App.tsx.
 */
export const PORTAL_CABINET_HREF = "/new/dashboard";
export const PORTAL_CABINET_RUNS_HREF = "/new/runs";
export const PORTAL_CABINET_VOLUNTEERING_HREF = "/new/volunteering";
export const PORTAL_CABINET_ACHIEVEMENTS_HREF = "/new/achievements";
export const PORTAL_CABINET_MEETINGS_HREF = "/new/co-runners";
export const PORTAL_CABINET_MAP_HREF = "/new/maps";
export const PORTAL_CABINET_HISTORY_HREF = "/new/history";
export const PORTAL_CABINET_SHARE_HREF = "/new/share";
export const PORTAL_CABINET_SETTINGS_HREF = "/new/settings";
