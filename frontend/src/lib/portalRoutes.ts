/**
 * Канонические адреса портального редизайна. С релиза (июль 2026) портал живёт
 * на основных путях сайта: "/", "/about", "/login". Старые адреса тёмного
 * запуска (/new, /new/about, /new/login) редиректят сюда — см. STATIC_ROUTES
 * в App.tsx. Все ссылки в features/portal/* берут адреса отсюда.
 */
export const PORTAL_HOME_HREF = "/";
export const PORTAL_ABOUT_HREF = "/about";
export const PORTAL_ABOUT_PRIVACY_HREF = `${PORTAL_ABOUT_HREF}#privacy`;
export const PORTAL_LOGIN_HREF = "/login";
