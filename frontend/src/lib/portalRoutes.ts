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
