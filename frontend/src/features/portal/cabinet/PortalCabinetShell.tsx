import { useState, type FormEvent, type ReactNode } from "react";
import { logout, updateDisplayName, type User } from "../../../lib/api";
import {
  PORTAL_ABOUT_HREF,
  PORTAL_CABINET_ACHIEVEMENTS_HREF,
  PORTAL_CABINET_HISTORY_HREF,
  PORTAL_CABINET_HREF,
  PORTAL_CABINET_MAP_HREF,
  PORTAL_CABINET_MEETINGS_HREF,
  PORTAL_CABINET_RUNS_HREF,
  PORTAL_CABINET_VOLUNTEERING_HREF,
  PORTAL_LOGIN_HREF,
} from "../../../lib/portalRoutes";
import { PortalHeader } from "../PortalHeader";
import "../portal.css";
import "./cabinet.css";

export type CabinetTabKey =
  | "dashboard"
  | "runs"
  | "volunteering"
  | "meetings"
  | "achievements"
  | "history"
  | "map";

type CabinetNavItem = {
  key: CabinetTabKey;
  href: string;
  label: string;
  icon: ReactNode;
};

// Иконки — инлайн-SVG в stroke-стиле (как в шапке портала), 20×20.
function icon(paths: ReactNode) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths}
    </svg>
  );
}

const NAV_ICONS: Record<CabinetTabKey, ReactNode> = {
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
};

const CABINET_NAV: CabinetNavItem[] = [
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
];

// Нижняя навигация телефона: 4 главных раздела + «Ещё» (остальное в шторке).
const BOTTOM_NAV_KEYS: CabinetTabKey[] = ["dashboard", "runs", "volunteering", "achievements"];

type SecondaryNavItem = { href: string; label: string; adminOnly?: boolean };

// Служебные разделы пока живут на старых адресах — до их собственной перевёрстки.
// Локации/Рейтинги сюда же: полноценные разделы сайта, но их портальный
// редизайн — отдельный этап (см. главную портала), здесь просто ссылки.
const SECONDARY_NAV: SecondaryNavItem[] = [
  { href: "/locations", label: "Локации" },
  { href: "/ratings", label: "Рейтинги" },
  { href: "/share", label: "Поделиться" },
  { href: "/settings", label: "Настройки" },
  { href: "/admin/users", label: "Админка", adminOnly: true },
  { href: PORTAL_ABOUT_HREF, label: "О проекте" },
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

function CabinetUserCard({ initialUser, collapsed = false }: { initialUser: User; collapsed?: boolean }) {
  const [user, setUser] = useState(initialUser);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const label = userLabel(user);

  if (collapsed) {
    return (
      <div className="portal-cab-user portal-cab-user-collapsed" title={label}>
        <span className="portal-cab-user-avatar" aria-hidden="true">
          {userInitials(label)}
        </span>
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
      <span className="portal-cab-user-avatar" aria-hidden="true">
        {userInitials(label)}
      </span>
      {!editing ? (
        <div className="portal-cab-user-info">
          <span className="portal-cab-user-name" title={label}>
            {label}
          </span>
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

type PortalCabinetShellProps = {
  active: CabinetTabKey;
  user: User;
  // Заголовок страницы; на дашборде шапку рисует сам контент (герой).
  title?: string;
  sub?: string;
  // Подменить адреса разделов. Нужно превью на демо-данных: там навигация
  // ведёт на ?tab=…, иначе клик уходит на страницу под RequireAuth и без
  // сессии выбрасывает на вход.
  hrefForTab?: (key: CabinetTabKey, defaultHref: string) => string;
  // Превью: служебные пункты и «Выйти» скрыты — они уводят из демо-режима.
  hideSecondaryNav?: boolean;
  children: ReactNode;
};

const SIDEBAR_COLLAPSED_KEY = "portalCabSidebarCollapsed";

export function PortalCabinetShell({
  active,
  user,
  title,
  sub,
  hrefForTab,
  hideSecondaryNav = false,
  children,
}: PortalCabinetShellProps) {
  const tabHref = (item: CabinetNavItem) =>
    hrefForTab ? hrefForTab(item.key, item.href) : item.href;
  const [moreOpen, setMoreOpen] = useState(false);
  // Свёрнутый сайдбар (узкий рельс-иконки) — выбор запоминается на устройстве.
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
    } catch {
      return false;
    }
  });

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

  const handleLogout = async () => {
    await logout();
    window.location.href = PORTAL_LOGIN_HREF;
  };

  const bottomItems = CABINET_NAV.filter((item) => BOTTOM_NAV_KEYS.includes(item.key));
  const moreItems = CABINET_NAV.filter((item) => !BOTTOM_NAV_KEYS.includes(item.key));
  const moreActive = moreItems.some((item) => item.key === active);

  return (
    <div className="portal-cab">
      {/* Та же шапка, что и на главной портала — с этого экрана вы уже
          авторизованы, так что навигация (Локации/Рейтинги/О проекте) и
          переход в кабинет по клику на ник работают идентично. */}
      <PortalHeader />

      <div className="portal-cab-layout">
        <aside className={`portal-cab-sidebar${collapsed ? " collapsed" : ""}`}>
          <CabinetUserCard initialUser={user} collapsed={collapsed} />

          <nav className="portal-cab-nav" aria-label="Разделы личного кабинета">
            {CABINET_NAV.map((item) => (
              <a
                key={item.key}
                href={tabHref(item)}
                className={`portal-cab-nav-item${item.key === active ? " active" : ""}`}
                aria-current={item.key === active ? "page" : undefined}
                title={collapsed ? item.label : undefined}
              >
                <span className="portal-cab-nav-icon">{item.icon}</span>
                <span className="portal-cab-nav-label">{item.label}</span>
              </a>
            ))}
          </nav>

          {!hideSecondaryNav && (
            <nav className="portal-cab-nav portal-cab-nav-secondary" aria-label="Служебные разделы">
              {SECONDARY_NAV.filter((item) => !item.adminOnly || user.is_admin).map((item) => (
                <a key={item.href} href={item.href} className="portal-cab-nav-item portal-cab-nav-item-secondary">
                  <span className="portal-cab-nav-label">{item.label}</span>
                </a>
              ))}
              <button
                type="button"
                className="portal-cab-nav-item portal-cab-nav-item-secondary portal-cab-logout"
                onClick={() => void handleLogout()}
              >
                <span className="portal-cab-nav-label">Выйти</span>
              </button>
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
              {icon(
                collapsed ? (
                  <path d="M9 6l6 6-6 6" />
                ) : (
                  <path d="M15 6l-6 6 6 6" />
                ),
              )}
            </span>
            <span className="portal-cab-nav-label">Свернуть</span>
          </button>
        </aside>

        <main className="portal-cab-main">
          {title && (
            <div className="portal-cab-pagehead">
              <h1>{title}</h1>
              {sub && <p className="portal-cab-pagehead-sub">{sub}</p>}
            </div>
          )}
          {children}
        </main>
      </div>

      {moreOpen && (
        <div
          className="portal-cab-more-backdrop"
          onClick={() => setMoreOpen(false)}
          role="presentation"
        >
          <div
            className="portal-cab-more-sheet"
            role="dialog"
            aria-label="Ещё разделы"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="portal-cab-more-grabber" aria-hidden="true" />
            {moreItems.map((item) => (
              <a
                key={item.key}
                href={tabHref(item)}
                className={`portal-cab-more-item${item.key === active ? " active" : ""}`}
              >
                <span className="portal-cab-nav-icon">{item.icon}</span>
                {item.label}
              </a>
            ))}
            {!hideSecondaryNav && (
              <>
                <div className="portal-cab-more-sep" />
                {SECONDARY_NAV.filter((item) => !item.adminOnly || user.is_admin).map((item) => (
                  <a key={item.href} href={item.href} className="portal-cab-more-item portal-cab-more-item-secondary">
                    {item.label}
                  </a>
                ))}
                <button
                  type="button"
                  className="portal-cab-more-item portal-cab-more-item-secondary portal-cab-logout"
                  onClick={() => void handleLogout()}
                >
                  Выйти
                </button>
              </>
            )}
          </div>
        </div>
      )}

      <nav className="portal-cab-bottomnav" aria-label="Разделы личного кабинета (телефон)">
        {bottomItems.map((item) => (
          <a
            key={item.key}
            href={tabHref(item)}
            className={`portal-cab-bottomnav-item${item.key === active ? " active" : ""}`}
            aria-current={item.key === active ? "page" : undefined}
          >
            <span className="portal-cab-nav-icon">{item.icon}</span>
            <span className="portal-cab-bottomnav-label">{item.label}</span>
          </a>
        ))}
        <button
          type="button"
          className={`portal-cab-bottomnav-item${moreActive || moreOpen ? " active" : ""}`}
          aria-expanded={moreOpen}
          onClick={() => setMoreOpen((open) => !open)}
        >
          <span className="portal-cab-nav-icon">
            {icon(
              <>
                <circle cx="5" cy="12" r="1.6" />
                <circle cx="12" cy="12" r="1.6" />
                <circle cx="19" cy="12" r="1.6" />
              </>,
            )}
          </span>
          <span className="portal-cab-bottomnav-label">Ещё</span>
        </button>
      </nav>
    </div>
  );
}
