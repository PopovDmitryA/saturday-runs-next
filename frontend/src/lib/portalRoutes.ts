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
