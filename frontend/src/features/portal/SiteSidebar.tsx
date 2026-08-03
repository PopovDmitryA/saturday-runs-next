/**
 * Единый сайдбар сайта (решение Дмитрия 25.07.2026): один и тот же компонент
 * в личном кабинете, Локациях, Рейтингах и Бэклоге. На главной портала
 * сайдбара нет. Устройство:
 *
 * - группа «Личный кабинет» раскрывается по клику и автоматически при
 *   переходе в любой его раздел; анониму — задизейблена, не скрыта;
 * - «Локации» — один пункт; на странице конкретной локации под ним
 *   появляются подпункты (сама локация и её журнал протоколов);
 * - «Рейтинги» — один пункт без перечня лидербордов (их будут десятки);
 * - служебный блок (Настройки/Бэклог/Админка/Выйти) виден на всех страницах;
 * - сворачивание в рельс-иконки работает везде (общий localStorage-ключ).
 */
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { logout, updateDisplayName, type User } from "../../lib/api";
import {
  cabinetTabHref,
  type CabinetTabSegmentKey,
  PORTAL_ABOUT_HREF,
  PORTAL_CABINET_ACHIEVEMENTS_HREF,
  PORTAL_CABINET_HISTORY_HREF,
  PORTAL_CABINET_HREF,
  PORTAL_CABINET_MAP_HREF,
  PORTAL_CABINET_MEETINGS_HREF,
  PORTAL_CABINET_RUNS_HREF,
  PORTAL_CABINET_SETTINGS_HREF,
  PORTAL_CABINET_SHARE_HREF,
  PORTAL_CABINET_VOLUNTEERING_HREF,
  PORTAL_LOGIN_HREF,
} from "../../lib/portalRoutes";
import { ImageLightbox } from "../../components/ImageLightbox";
import { clearCachedUser, useOptionalUser } from "../../lib/useOptionalUser";
import "./cabinet/cabinet.css";

// "settings" — служебная страница без пункта в основной навигации (живёт под
// разделительной линией), поэтому её ключ не участвует в NAV_ICONS/CABINET_NAV.
export type CabinetTabKey =
  | "dashboard"
  | "runs"
  | "volunteering"
  | "meetings"
  | "achievements"
  | "history"
  | "map"
  | "share"
  | "settings";

/** Что подсвечивать: вкладка ЛК, раздел сайта или ничего. */
export type SiteSidebarActive = CabinetTabKey | "locations" | "ratings" | "backlog" | null;

type CabinetNavItem = {
  key: CabinetTabKey;
  /** Служебный адрес (fallback, пока хендл участника неизвестен). */
  href: string;
  label: string;
  icon: ReactNode;
};

/**
 * Адрес вкладки для конкретного пользователя: разделы кабинета живут на его
 * публичном адресе /users/{хендл}/…, а служебные (Поделиться, Настройки)
 * остаются на собственных путях — публиковать их незачем.
 */
function navHref(user: User | null | undefined, item: CabinetNavItem): string {
  if (item.key === "share" || item.key === "settings") {
    return item.href;
  }
  return cabinetTabHref(user ?? null, item.key as CabinetTabSegmentKey);
}

// Иконки — инлайн-SVG в stroke-стиле (как в шапке портала), 20×20.
export function icon(paths: ReactNode) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths}
    </svg>
  );
}

const NAV_ICONS: Record<Exclude<CabinetTabKey, "settings">, ReactNode> = {
  dashboard: icon(
    <>
      <rect x="3" y="3" width="7.5" height="9" rx="1.6" />
      <rect x="13.5" y="3" width="7.5" height="5.5" rx="1.6" />
      <rect x="13.5" y="12" width="7.5" height="9" rx="1.6" />
      <rect x="3" y="15.5" width="7.5" height="5.5" rx="1.6" />
    </>,
  ),
  runs: icon(<polyline points="3,17 8,12 11,15 16,8 18.5,10.5 21,5" />),
  volunteering: icon(
    <path d="M12 20.5c-4.6-3.4-8-6.3-8-9.9C4 7.9 6 6 8.4 6c1.5 0 2.8.8 3.6 2 .8-1.2 2.1-2 3.6-2C18 6 20 7.9 20 10.6c0 3.6-3.4 6.5-8 9.9Z" />,
  ),
  meetings: icon(
    <>
      <circle cx="8.5" cy="8.5" r="3.2" />
      <path d="M2.8 20c.6-3 2.9-4.8 5.7-4.8s5.1 1.8 5.7 4.8" />
      <circle cx="16.8" cy="9.8" r="2.6" />
      <path d="M15.2 15.6c.5-.2 1-.3 1.6-.3 2.4 0 4.3 1.5 4.9 4" />
    </>,
  ),
  achievements: icon(
    <>
      <circle cx="12" cy="9" r="5.2" />
      <path d="M8.8 13.4 7 21l5-2.6L17 21l-1.8-7.6" />
    </>,
  ),
  history: icon(
    <>
      <circle cx="12" cy="12" r="8.5" />
      <polyline points="12,7 12,12 15.5,14" />
    </>,
  ),
  map: icon(
    <>
      <path d="M9 4 3.5 6v14L9 18l6 2 5.5-2V4L15 6 9 4Z" />
      <path d="M9 4v14M15 6v14" />
    </>,
  ),
  share: icon(
    <>
      <circle cx="6" cy="12" r="2.6" />
      <circle cx="17.5" cy="5.5" r="2.6" />
      <circle cx="17.5" cy="18.5" r="2.6" />
      <path d="M8.4 10.7 15.1 6.8M8.4 13.3l6.7 3.9" />
    </>,
  ),
};

const CABINET_ICON = icon(
  <>
    <circle cx="12" cy="8" r="3.6" />
    <path d="M5 20c.8-3.6 3.6-5.7 7-5.7s6.2 2.1 7 5.7" />
  </>,
);

const LOCATIONS_ICON = icon(
  <>
    <path d="M12 21s-7-5.6-7-11a7 7 0 0 1 14 0c0 5.4-7 11-7 11Z" />
    <circle cx="12" cy="10" r="2.6" />
  </>,
);

const PROFILE_ICON = icon(
  <>
    <circle cx="12" cy="8" r="3.4" />
    <path d="M5.5 20a6.8 6.8 0 0 1 13 0" />
    <path d="M17.5 3.5 19 5l2.5-2.5" />
  </>,
);

// Подпункты открытой локации: сама площадка и её журнал протоколов.
const LOCATION_PIN_ICON = icon(
  <>
    <circle cx="12" cy="12" r="3.2" />
    <path d="M12 3v2.2M12 18.8V21M3 12h2.2M18.8 12H21" />
  </>,
);

const PROTOCOL_ICON = icon(
  <>
    <path d="M6 3.5h9L19 7.5V20a.5.5 0 0 1-.5.5h-12A.5.5 0 0 1 6 20V4a.5.5 0 0 1 .5-.5Z" />
    <path d="M14.5 3.5V8H19" />
    <path d="M9 12.5h6M9 16h4" />
  </>,
);

const RATINGS_ICON = icon(
  <>
    <path d="M7.5 4h9v4.5a4.5 4.5 0 0 1-9 0V4Z" />
    <path d="M7.5 5.5H5a3 3 0 0 0 2.8 3.9M16.5 5.5H19a3 3 0 0 1-2.8 3.9" />
    <path d="M12 13v3.5M8.5 21h7M10 21l.7-4.5h2.6l.7 4.5" />
  </>,
);

export const CABINET_NAV: CabinetNavItem[] = [
  { key: "dashboard", href: PORTAL_CABINET_HREF, label: "Обзор", icon: NAV_ICONS.dashboard },
  { key: "runs", href: PORTAL_CABINET_RUNS_HREF, label: "Пробежки", icon: NAV_ICONS.runs },
  {
    key: "volunteering",
    href: PORTAL_CABINET_VOLUNTEERING_HREF,
    label: "Волонтёрство",
    icon: NAV_ICONS.volunteering,
  },
  {
    key: "achievements",
    href: PORTAL_CABINET_ACHIEVEMENTS_HREF,
    label: "Достижения",
    icon: NAV_ICONS.achievements,
  },
  { key: "meetings", href: PORTAL_CABINET_MEETINGS_HREF, label: "Встречи", icon: NAV_ICONS.meetings },
  { key: "history", href: PORTAL_CABINET_HISTORY_HREF, label: "Моя история", icon: NAV_ICONS.history },
  { key: "map", href: PORTAL_CABINET_MAP_HREF, label: "Карта", icon: NAV_ICONS.map },
  { key: "share", href: PORTAL_CABINET_SHARE_HREF, label: "Поделиться", icon: NAV_ICONS.share },
];

export type SecondaryNavItem = { href: string; label: string; adminOnly?: boolean; authOnly?: boolean };

/**
 * Публичные разделы сайта. В сайдбаре они рисуются отдельными ссылками (у
 * «Локаций» бывает подпункт с текущей площадкой), но список нужен и нижней
 * навигации телефона: без него «Локации» и «Рейтинги» не попадали в шторку
 * «Ещё» и на телефоне были недостижимы вовсе.
 */
export const SITE_SECTIONS_NAV: { key: "locations" | "ratings"; href: string; label: string; icon: ReactNode }[] = [
  { key: "locations", href: "/locations", label: "Локации", icon: LOCATIONS_ICON },
  { key: "ratings", href: "/ratings", label: "Рейтинги", icon: RATINGS_ICON },
];

// Служебные разделы — видны на всех страницах с сайдбаром.
// «Админка» подсвечена янтарным (см. .portal-cab-nav-item-admin).
export const SECONDARY_NAV: SecondaryNavItem[] = [
  { href: PORTAL_CABINET_SETTINGS_HREF, label: "Настройки", authOnly: true },
  { href: PORTAL_ABOUT_HREF, label: "О проекте" },
  { href: "/backlog", label: "Бэклог" },
  { href: "/admin/users", label: "Админка", adminOnly: true },
];

// Экспорт: имя пользователя нужно и герою дашборда.
export function userLabel(user: User): string {
  const customName = user.display_name?.trim();
  if (user.display_name_customized === true && customName) {
    return customName;
  }
  if (user.telegram_username) {
    const login = user.telegram_username.replace(/^@/, "");
    return `@${login}`;
  }
  if (customName) {
    return customName;
  }
  return `Участник ${user.telegram_id ?? user.id.slice(0, 8)}`;
}

function userInitials(label: string): string {
  const clean = label.replace(/^@/, "").trim();
  const parts = clean.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return clean.slice(0, 2).toUpperCase();
}

/**
 * Карточка участника: аватар, имя и «✎» — правка отображаемого имени.
 * Экспортируется, потому что на телефоне сайдбар скрыт целиком, и кабинет
 * рисует эту же карточку над контентом (иначе имя правилось только с
 * компьютера — баг, 29.07.2026).
 */
export function CabinetUserCard({ initialUser, collapsed = false }: { initialUser: User; collapsed?: boolean }) {
  const [user, setUser] = useState(initialUser);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [avatarZoomed, setAvatarZoomed] = useState(false);

  const label = userLabel(user);

  // Аватарка кликабельна: открывает оригинал без пережатия (просьба Дмитрия
  // 29.07.2026 — «по клику на аватарку должно в сайдбаре открываться»).
  const avatar = user.avatar_url ? (
    <>
      <button
        type="button"
        className="portal-cab-user-avatar portal-cab-user-avatar-img portal-cab-user-avatar-button"
        onClick={() => setAvatarZoomed(true)}
        aria-label="Открыть аватар"
        title="Открыть аватар"
      >
        <img src={user.avatar_url} alt="" />
      </button>
      {avatarZoomed && (
        <ImageLightbox
          src={user.avatar_full_url || user.avatar_url}
          alt={label}
          onClose={() => setAvatarZoomed(false)}
        />
      )}
    </>
  ) : (
    <span className="portal-cab-user-avatar" aria-hidden="true">
      {userInitials(label)}
    </span>
  );

  if (collapsed) {
    return (
      <div className="portal-cab-user portal-cab-user-collapsed" title={label}>
        {avatar}
      </div>
    );
  }

  const handleSave = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const updated = await updateDisplayName(draft);
      setUser(updated);
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить имя");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="portal-cab-user">
      {avatar}
      {!editing ? (
        <div className="portal-cab-user-info">
          {/* Имя — ссылка в обзор кабинета (просьба Дмитрия 26.07.2026):
              раньше по нему кликали и ничего не происходило. */}
          <a className="portal-cab-user-name" href={cabinetTabHref(user, "dashboard")} title={label}>
            {label}
          </a>
          <button
            type="button"
            className="portal-cab-user-edit"
            aria-label="Изменить имя"
            title="Изменить имя"
            onClick={() => {
              setDraft(
                user.display_name_customized === true && user.display_name ? user.display_name : "",
              );
              setEditing(true);
              setError(null);
            }}
          >
            ✎
          </button>
        </div>
      ) : (
        <form className="portal-cab-user-form" onSubmit={(event) => void handleSave(event)}>
          <input
            className="input portal-cab-user-input"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            maxLength={128}
            placeholder={
              user.telegram_username
                ? `Пусто — будет @${user.telegram_username.replace(/^@/, "")}`
                : "Пусто — имя из Telegram"
            }
            autoFocus
          />
          <div className="portal-cab-user-form-actions">
            <button type="submit" className="btn btn-ghost btn-sm" disabled={saving}>
              {saving ? "…" : "OK"}
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={saving}
              onClick={() => {
                setEditing(false);
                setError(null);
              }}
            >
              Отмена
            </button>
          </div>
        </form>
      )}
      {error && <p className="portal-cab-user-error">{error}</p>}
    </div>
  );
}

const SIDEBAR_COLLAPSED_KEY = "portalCabSidebarCollapsed";
// Раскрыта ли группа «Личный кабинет». По умолчанию раскрыта; выбор помнится
// между страницами (сайт — MPA, без этого свёрнутая группа раскрывалась
// заново на каждом переходе).
const CABINET_GROUP_OPEN_KEY = "portalCabGroupOpen";

const CABINET_TAB_KEYS: readonly SiteSidebarActive[] = [
  "dashboard",
  "runs",
  "volunteering",
  "meetings",
  "achievements",
  "history",
  "map",
  "share",
  "settings",
];

export function isCabinetTab(active: SiteSidebarActive): active is CabinetTabKey {
  return active !== null && CABINET_TAB_KEYS.includes(active);
}

export type SidebarExtraGroup = {
  /** Заголовок группы (например, имя участника на публичном профиле). */
  title: string;
  /**
   * Клик по заголовку. Задан — заголовок становится кнопкой: на публичном
   * профиле имя участника ведёт на его главную, как ожидается от «шапки»
   * раздела (репорт Дмитрия 04.08.2026 — раньше клик не делал ничего).
   */
  onTitleClick?: () => void;
  items: { key: string; label: string; active: boolean; onClick: () => void }[];
};

export type SiteSidebarProps = {
  active: SiteSidebarActive;
  /** User — залогинен, null — аноним, undefined — определить самостоятельно
      (публичные разделы, где нет своего гейта). */
  user?: User | null;
  /** Открытая локация — под пунктом «Локации» появляются её подпункты. */
  location?: { slug: string; name: string };
  /** Превью ЛК: подменить адреса вкладок (?tab=…). */
  hrefForTab?: (key: CabinetTabKey, defaultHref: string) => string;
  /** Превью ЛК: скрыть служебные пункты и «Выйти». */
  hideSecondaryNav?: boolean;
  /** Сообщить контейнеру о сворачивании (пересчёт офсета модалок в ЛК). */
  onCollapsedChange?: (collapsed: boolean) => void;
  /** Доп. группа вкладок текущей страницы (публичный профиль участника). */
  extraGroup?: SidebarExtraGroup;
};

export function SiteSidebar({
  active,
  user: userProp,
  location,
  hrefForTab,
  hideSecondaryNav = false,
  onCollapsedChange,
  extraGroup,
}: SiteSidebarProps) {
  // Хук вызывается всегда (правила хуков); если user передан пропом — он главнее.
  const detectedUser = useOptionalUser();
  // undefined — сессия ещё проверяется (первый заход без кэша): рисуем
  // нейтральное состояние, чтобы залогиненному не мигало «Войти» и дизейблы.
  const user = userProp !== undefined ? userProp : detectedUser;
  const authed = user != null;
  const anon = user === null;

  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
    } catch {
      return false;
    }
  });
  // Группа ЛК по умолчанию РАЗВЁРНУТА (решение Дмитрия 25.07.2026), но выбор
  // запоминается: свернул — остаётся свёрнутой и на других страницах.
  const [cabinetOpen, setCabinetOpen] = useState(() => {
    try {
      return localStorage.getItem(CABINET_GROUP_OPEN_KEY) !== "0";
    } catch {
      return true;
    }
  });

  useEffect(() => {
    onCollapsedChange?.(collapsed);
  }, [collapsed, onCollapsedChange]);

  const toggleCollapsed = () => {
    setCollapsed((current) => {
      const next = !current;
      try {
        localStorage.setItem(SIDEBAR_COLLAPSED_KEY, next ? "1" : "0");
      } catch {
        // localStorage недоступен — просто не запоминаем
      }
      return next;
    });
  };

  const setCabinetOpenPersisted = (next: boolean) => {
    setCabinetOpen(next);
    try {
      localStorage.setItem(CABINET_GROUP_OPEN_KEY, next ? "1" : "0");
    } catch {
      // localStorage недоступен — просто не запоминаем
    }
  };

  const toggleCabinetOpen = () => setCabinetOpenPersisted(!cabinetOpen);

  const handleLogout = async () => {
    await logout();
    clearCachedUser();
    window.location.href = PORTAL_LOGIN_HREF;
  };

  const tabHref = (item: CabinetNavItem) =>
    hrefForTab ? hrefForTab(item.key, item.href) : navHref(user, item);
  const pathname = typeof window !== "undefined" ? window.location.pathname : "";

  return (
    <aside className={`portal-cab-sidebar${collapsed ? " collapsed" : ""}`}>
      {authed && <CabinetUserCard initialUser={user} collapsed={collapsed} />}
      {anon && (
        <div className={`portal-cab-user portal-cab-login-card${collapsed ? " portal-cab-user-collapsed" : ""}`}>
          {collapsed ? (
            <a className="portal-cab-user-avatar portal-cab-login-avatar" href={PORTAL_LOGIN_HREF} title="Войти">
              →
            </a>
          ) : (
            <a className="btn primary btn-sm portal-cab-login-btn" href={PORTAL_LOGIN_HREF}>
              Войти
            </a>
          )}
        </div>
      )}
      {user === undefined && (
        <div className="portal-cab-user portal-cab-user-pending" aria-hidden="true" />
      )}

      <nav className="portal-cab-nav" aria-label="Разделы сайта">
        {/* Группа «Личный кабинет»: клик по тексту — переход в обзор (группа
            раскроется сама на странице ЛК), клик по стрелке — только
            раскрыть/скрыть. Анониму видна, но задизейблена. */}
        <a
          href={
            authed
              ? hrefForTab
                ? hrefForTab("dashboard", PORTAL_CABINET_HREF)
                : cabinetTabHref(user, "dashboard")
              : undefined
          }
          className={`portal-cab-nav-item portal-cab-group-head${
            isCabinetTab(active) ? " portal-cab-group-head-current" : ""
          }${anon ? " portal-cab-nav-item-disabled" : ""}`}
          aria-disabled={anon || undefined}
          onClick={() => {
            // Клик по названию ведёт в обзор и заодно раскрывает группу —
            // чтобы после перехода разделы кабинета были видны сразу.
            if (authed && !cabinetOpen) {
              setCabinetOpenPersisted(true);
            }
          }}
          title={
            anon
              ? "Войдите на сайт, чтобы открыть личный кабинет: ваши пробежки, волонтёрства, достижения и карта локаций"
              : collapsed
                ? "Личный кабинет"
                : undefined
          }
        >
          <span className="portal-cab-nav-icon">{CABINET_ICON}</span>
          <span className="portal-cab-nav-label">Личный кабинет</span>
          {authed && (
            <button
              type="button"
              className={`portal-cab-group-chevron${cabinetOpen ? " open" : ""}`}
              aria-label={cabinetOpen ? "Скрыть разделы кабинета" : "Показать разделы кабинета"}
              aria-expanded={cabinetOpen}
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                toggleCabinetOpen();
              }}
            >
              {icon(<path d="M9 6l6 6-6 6" />)}
            </button>
          )}
        </a>
        {authed &&
          cabinetOpen &&
          CABINET_NAV.map((item) => (
            <a
              key={item.key}
              href={tabHref(item)}
              className={`portal-cab-nav-item portal-cab-nav-subitem${item.key === active ? " active" : ""}`}
              aria-current={item.key === active ? "page" : undefined}
              title={collapsed ? item.label : undefined}
            >
              <span className="portal-cab-nav-icon">{item.icon}</span>
              <span className="portal-cab-nav-label">{item.label}</span>
            </a>
          ))}

        <a
          href="/locations"
          className={`portal-cab-nav-item${active === "locations" && !location ? " active" : ""}`}
          aria-current={active === "locations" && !location ? "page" : undefined}
          title={collapsed ? "Локации" : undefined}
        >
          <span className="portal-cab-nav-icon">{LOCATIONS_ICON}</span>
          <span className="portal-cab-nav-label">Локации</span>
        </a>
        {location && (
          <>
            <a
              href={`/locations/${location.slug}`}
              className={`portal-cab-nav-item portal-cab-nav-subitem${
                pathname === `/locations/${location.slug}` ? " active" : ""
              }`}
              title={collapsed ? location.name : undefined}
            >
              <span className="portal-cab-nav-icon">{LOCATION_PIN_ICON}</span>
              <span className="portal-cab-nav-label">{location.name}</span>
            </a>
            <a
              href={`/locations/${location.slug}/events`}
              className={`portal-cab-nav-item portal-cab-nav-subitem${
                pathname.endsWith("/events") ? " active" : ""
              }`}
              title={collapsed ? "Журнал протоколов" : undefined}
            >
              <span className="portal-cab-nav-icon">{PROTOCOL_ICON}</span>
              <span className="portal-cab-nav-label">Журнал протоколов</span>
            </a>
          </>
        )}

        <a
          href="/ratings"
          className={`portal-cab-nav-item${active === "ratings" ? " active" : ""}`}
          aria-current={active === "ratings" ? "page" : undefined}
          title={collapsed ? "Рейтинги" : undefined}
        >
          <span className="portal-cab-nav-icon">{RATINGS_ICON}</span>
          <span className="portal-cab-nav-label">Рейтинги</span>
        </a>

        {/* Группа текущей страницы (публичный профиль участника): заголовок —
            имя, подпункты — вкладки профиля, переключаются без перезагрузки. */}
        {extraGroup && (
          <>
            {/* Линия-разделитель: пункты чужого профиля временные, их надо
                визуально отделить от постоянной навигации сайта. */}
            <div className="portal-cab-nav-sep" aria-hidden="true" />
            {extraGroup.onTitleClick ? (
              <button
                type="button"
                onClick={extraGroup.onTitleClick}
                className="portal-cab-nav-item portal-cab-group-head portal-cab-nav-textitem"
                title={collapsed ? extraGroup.title : undefined}
              >
                <span className="portal-cab-nav-icon">{PROFILE_ICON}</span>
                <span className="portal-cab-nav-label">{extraGroup.title}</span>
              </button>
            ) : (
              <div
                className="portal-cab-nav-item portal-cab-group-head portal-cab-group-head-static"
                title={collapsed ? extraGroup.title : undefined}
              >
                <span className="portal-cab-nav-icon">{PROFILE_ICON}</span>
                <span className="portal-cab-nav-label">{extraGroup.title}</span>
              </div>
            )}
            {extraGroup.items.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={item.onClick}
                className={`portal-cab-nav-item portal-cab-nav-subitem portal-cab-nav-textitem${
                  item.active ? " active" : ""
                }`}
                aria-current={item.active ? "page" : undefined}
              >
                <span className="portal-cab-nav-label">{item.label}</span>
              </button>
            ))}
          </>
        )}
      </nav>

      {!hideSecondaryNav && (
        <nav className="portal-cab-nav portal-cab-nav-secondary" aria-label="Служебные разделы">
          {SECONDARY_NAV.filter((item) => !item.adminOnly || (user != null && user.is_admin)).map((item) => {
            const disabled = Boolean(item.authOnly) && anon;
            const current =
              (item.href === PORTAL_CABINET_SETTINGS_HREF && active === "settings") ||
              (item.href === "/backlog" && active === "backlog");
            return (
              <a
                key={item.href}
                href={disabled ? undefined : item.href}
                className={`portal-cab-nav-item portal-cab-nav-item-secondary${
                  item.adminOnly ? " portal-cab-nav-item-admin" : ""
                }${disabled ? " portal-cab-nav-item-disabled" : ""}${current ? " active" : ""}`}
                aria-disabled={disabled || undefined}
                title={disabled ? "Доступно после входа на сайт" : undefined}
              >
                <span className="portal-cab-nav-label">{item.label}</span>
              </a>
            );
          })}
          {authed && (
            <button
              type="button"
              className="portal-cab-nav-item portal-cab-nav-item-secondary portal-cab-logout"
              onClick={() => void handleLogout()}
            >
              <span className="portal-cab-nav-label">Выйти</span>
            </button>
          )}
        </nav>
      )}

      <button
        type="button"
        className="portal-cab-collapse"
        onClick={toggleCollapsed}
        aria-label={collapsed ? "Развернуть меню" : "Свернуть меню"}
        title={collapsed ? "Развернуть меню" : "Свернуть меню"}
      >
        <span className="portal-cab-nav-icon">
          {icon(collapsed ? <path d="M9 6l6 6-6 6" /> : <path d="M15 6l-6 6 6 6" />)}
        </span>
        <span className="portal-cab-nav-label">Свернуть</span>
      </button>
    </aside>
  );
}
