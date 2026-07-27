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
export const PORTAL_CABINET_SHARE_HREF = "/share";
export const PORTAL_CABINET_SETTINGS_HREF = "/settings";

/**
 * Публичный адрес участника — он же адрес его кабинета (решение Дмитрия
 * 26.07.2026). Идея: бегун видит в строке браузера ссылку, которой можно
 * поделиться, а не служебный /new/dashboard. Свой хендл открывает полный
 * кабинет, чужой — гостевой профиль.
 */
export type ProfileHandleSource = {
  serial_id?: number | null;
  public_slug?: string | null;
};

/** Хендл для адреса: индивидуальная ссылка, иначе номер участника. */
export function profileHandle(user: ProfileHandleSource): string | null {
  const slug = user.public_slug?.trim();
  if (slug) {
    return slug;
  }
  return user.serial_id != null ? String(user.serial_id) : null;
}

/** Базовый адрес профиля: /users/{хендл}. */
export function profileBaseHref(user: ProfileHandleSource): string | null {
  const handle = profileHandle(user);
  return handle ? `/users/${encodeURIComponent(handle)}` : null;
}

/** Сегменты вкладок кабинета в публичном адресе (обзор — пустой). */
export const CABINET_TAB_SEGMENTS = {
  dashboard: "",
  runs: "runs",
  volunteering: "volunteering",
  achievements: "achievements",
  meetings: "co-runners",
  map: "maps",
  history: "history",
} as const;

export type CabinetTabSegmentKey = keyof typeof CABINET_TAB_SEGMENTS;

/**
 * Адрес вкладки кабинета для конкретного пользователя. Пока хендл неизвестен
 * (сессия ещё грузится), отдаём служебные адреса — они редиректят на
 * публичные, как только пользователь определён.
 */
export function cabinetTabHref(
  user: ProfileHandleSource | null | undefined,
  tab: CabinetTabSegmentKey,
): string {
  const base = user ? profileBaseHref(user) : null;
  if (!base) {
    return LEGACY_CABINET_HREFS[tab];
  }
  const segment = CABINET_TAB_SEGMENTS[tab];
  return segment ? `${base}/${segment}` : base;
}

/**
 * Адрес вкладки чужого профиля: /users/{хендл}[/сегмент].
 *
 * Нужен, чтобы вкладками публичного профиля можно было делиться: раньше они
 * переключались только во внутреннем состоянии, и в адресной строке всегда
 * оставалась главная страница участника.
 */
export function profileTabHref(handle: string, tab: CabinetTabSegmentKey): string {
  const segment = CABINET_TAB_SEGMENTS[tab];
  const base = `/users/${encodeURIComponent(handle)}`;
  return segment ? `${base}/${segment}` : base;
}

/** Служебные адреса кабинета — вход по ним редиректит на публичный. */
export const LEGACY_CABINET_HREFS: Record<CabinetTabSegmentKey, string> = {
  dashboard: PORTAL_CABINET_HREF,
  runs: PORTAL_CABINET_RUNS_HREF,
  volunteering: PORTAL_CABINET_VOLUNTEERING_HREF,
  achievements: PORTAL_CABINET_ACHIEVEMENTS_HREF,
  meetings: PORTAL_CABINET_MEETINGS_HREF,
  map: PORTAL_CABINET_MAP_HREF,
  history: PORTAL_CABINET_HISTORY_HREF,
};

/** Совпадает ли хендл из адреса с текущим пользователем. */
export function isOwnHandle(user: ProfileHandleSource | null | undefined, handle: string): boolean {
  if (!user) {
    return false;
  }
  const normalized = handle.trim().toLowerCase();
  const slug = user.public_slug?.trim().toLowerCase();
  if (slug && slug === normalized) {
    return true;
  }
  return user.serial_id != null && String(user.serial_id) === normalized;
}
