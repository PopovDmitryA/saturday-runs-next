/**
 * Навигация сайта на компьютере — вариант В (решение Дмитрия 23.09.2026):
 * узкий рельс разделов с иконками и колонка со ВСЕМИ страницами текущего
 * раздела. Раньше был один длинный сайдбар, где подпункты появлялись и
 * исчезали, 14 рейтингов прятались за одним пунктом, а кабинет организатора
 * жил подпунктом «Локаций».
 *
 * Состав пунктов — не здесь, а в nav/siteNav.ts: оттуда же рисуются нижняя
 * панель телефона, полоса страниц раздела, шторка «Меню» и поиск по страницам.
 *
 * Интерфейс компонента прежний (active, location, user, extraGroup…), поэтому
 * три десятка страниц менять не пришлось.
 */
import { useEffect, useState, type ReactNode } from "react";
import { logout, type User } from "../../lib/api";
import { PORTAL_DISPLAY_NAME_SETTINGS_HREF, PORTAL_LOGIN_HREF, cabinetTabHref } from "../../lib/portalRoutes";
import { ImageLightbox } from "../../components/ImageLightbox";
import { clearCachedUser, useOptionalUser } from "../../lib/useOptionalUser";
import {
  CABINET_ICONS,
  CHEVRON_LEFT_ICON,
  CHEVRON_RIGHT_ICON,
  LOCATIONS_ICON,
  ME_ICON,
  ORGANIZER_ICON,
  PROFILE_ICON,
  PROJECT_ICON,
  RATINGS_ICON,
  RESULTS_ICON,
  SEARCH_ICON,
  SETTINGS_ICON,
} from "./nav/navIcons";
import { resolveNavState, type SiteSidebarActive } from "./nav/navState";
import { OrganizerSwitcher } from "./nav/OrganizerSwitcher";
import { openSiteSearch } from "./nav/siteSearchBus";
import { isLinkCurrent, type CabinetTabKey, type NavSection, type NavSectionKey, type NavPlace } from "./nav/siteNav";
import "./cabinet/cabinet.css";
import "./nav/siteNav.css";

export { icon } from "./nav/navIcons";
export { isCabinetTab, type SiteSidebarActive } from "./nav/navState";
export type { CabinetTabKey } from "./nav/siteNav";

/** Иконки вкладок кабинета — ими же рисует вкладки чужой профиль. */
export const NAV_ICONS = CABINET_ICONS;

export const SECTION_ICONS: Record<NavSectionKey, ReactNode> = {
  me: ME_ICON,
  organizer: ORGANIZER_ICON,
  results: RESULTS_ICON,
  locations: LOCATIONS_ICON,
  ratings: RATINGS_ICON,
  project: PROJECT_ICON,
  account: SETTINGS_ICON,
};

// Экспорт: имя пользователя нужно и герою дашборда.
// display_name с 25.08.2026 считается на сервере из профилей беговых систем,
// поэтому фронту выбирать больше не из чего — только запасные варианты на
// случай, если сервер имени так и не нашёл.
export function userLabel(user: User): string {
  const name = user.display_name?.trim();
  if (name) {
    return name;
  }
  if (user.telegram_username) {
    return `@${user.telegram_username.replace(/^@/, "")}`;
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
 * Экспортируется, потому что на телефоне колонки нет, и кабинет рисует эту же
 * карточку над контентом (иначе имя правилось только с компьютера — баг,
 * 29.07.2026).
 */
export function CabinetUserCard({ initialUser }: { initialUser: User }) {
  const [avatarZoomed, setAvatarZoomed] = useState(false);
  const user = initialUser;
  const label = userLabel(user);

  // Аватарка кликабельна: открывает оригинал без пережатия (просьба Дмитрия
  // 29.07.2026 — «по клику на аватарку должно в сайдбаре открываться»).
  const avatar = user.avatar_url ? (
    <>
      <button
        type="button"
        className="portal-cab-user-avatar portal-cab-user-avatar-button"
        onClick={() => setAvatarZoomed(true)}
        aria-label="Открыть аватарку"
        title="Открыть аватарку"
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

  return (
    <div className="portal-cab-user">
      {avatar}
      <div className="portal-cab-user-info">
        {/* Имя — ссылка в обзор кабинета (просьба Дмитрия 26.07.2026). */}
        <a className="portal-cab-user-name" href={cabinetTabHref(user, "dashboard")} title={label}>
          {label}
        </a>
        {/* Настройка имени живёт в «Настройках» (решение Дмитрия 25.08.2026). */}
        <a
          className="portal-cab-user-edit"
          href={PORTAL_DISPLAY_NAME_SETTINGS_HREF}
          aria-label="Изменить имя"
          title="Изменить имя"
        >
          ✎
        </a>
      </div>
    </div>
  );
}

// Свёрнута ли колонка (остаётся только рельс) — помнится между страницами:
// широким таблицам протоколов и рейтингов место нужнее, чем меню.
const COLUMN_COLLAPSED_KEY = "portalCabSidebarCollapsed";

export type SidebarExtraGroup = {
  /** Заголовок группы (например, имя участника на публичном профиле). */
  title: string;
  /** Аватарка участника — вместо родовой иконки профиля в заголовке группы. */
  avatarUrl?: string | null;
  /** Клик по заголовку: на публичном профиле имя ведёт на его главную. */
  onTitleClick?: () => void;
  items: { key: string; label: string; icon?: ReactNode; active: boolean; onClick: () => void }[];
};

export type SiteSidebarProps = {
  active: SiteSidebarActive;
  /** User — залогинен, null — аноним, undefined — определить самостоятельно. */
  user?: User | null;
  /** Открытая локация (или локация кабинета организатора). */
  location?: NavPlace;
  /** Превью ЛК: подменить адреса вкладок (?tab=…). */
  hrefForTab?: (key: CabinetTabKey, defaultHref: string) => string;
  /** Превью ЛК: скрыть служебные пункты и «Выйти». */
  hideSecondaryNav?: boolean;
  /** Сообщить контейнеру о сворачивании (пересчёт офсета модалок в ЛК). */
  onCollapsedChange?: (collapsed: boolean) => void;
  /** Доп. группа вкладок текущей страницы (публичный профиль участника). */
  extraGroup?: SidebarExtraGroup;
};

function isMac(): boolean {
  return typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
}

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
  const user = userProp !== undefined ? userProp : detectedUser;

  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(COLUMN_COLLAPSED_KEY) === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    onCollapsedChange?.(collapsed);
  }, [collapsed, onCollapsedChange]);

  const toggleCollapsed = () => {
    setCollapsed((current) => {
      const next = !current;
      try {
        localStorage.setItem(COLUMN_COLLAPSED_KEY, next ? "1" : "0");
      } catch {
        // localStorage недоступен — просто не запоминаем
      }
      return next;
    });
  };

  const { pathname, sections, current, organizerPlace } = resolveNavState({ active, user, location, hrefForTab });
  // В рельсе — только места, куда ходят за статистикой: «О проекте» и
  // аккаунт живут в шапке (см. inRail в siteNav.ts).
  const visibleSections = sections.filter((section) =>
    hideSecondaryNav ? section.key === "me" : section.inRail !== false,
  );
  // Колонка чужого профиля показывает его вкладки; иначе — текущий раздел, а
  // если раздела нет (например, 404) — свой кабинет.
  const columnSection = current ?? (extraGroup ? null : sections[0]);

  return (
    <aside className={`site-nav${collapsed ? " site-nav-collapsed" : ""}`} aria-label="Навигация по сайту">
      <nav className="site-rail" aria-label="Разделы сайта">
        {!hideSecondaryNav && (
          <button
            type="button"
            className="site-rail-item site-rail-search"
            onClick={() => openSiteSearch()}
            title={`Поиск по сайту (${isMac() ? "⌘" : "Ctrl"}+K)`}
          >
            <span className="site-rail-icon">{SEARCH_ICON}</span>
            <span className="site-rail-label">Поиск</span>
          </button>
        )}
        {visibleSections.map((section) => {
          const isCurrent = section.key === current?.key;
          return (
            <a
              key={section.key}
              href={section.href}
              className={`site-rail-item${isCurrent ? " active" : ""}`}
              aria-current={isCurrent ? "true" : undefined}
              title={section.label}
            >
              <span className="site-rail-icon">{SECTION_ICONS[section.key]}</span>
              <span className="site-rail-label">{section.shortLabel}</span>
            </a>
          );
        })}
        <button
          type="button"
          className="site-rail-item site-rail-collapse"
          onClick={toggleCollapsed}
          aria-label={collapsed ? "Показать подразделы" : "Скрыть подразделы"}
          title={collapsed ? "Показать подразделы" : "Скрыть подразделы — больше места таблицам"}
        >
          <span className="site-rail-icon">{collapsed ? CHEVRON_RIGHT_ICON : CHEVRON_LEFT_ICON}</span>
        </button>
      </nav>

      {!collapsed && (
        <div className="site-col">
          {/* Карточки участника в колонке больше нет (25.09.2026): имя с
              аватаркой уже есть в шапке и открывает меню аккаунта, а без
              карточки заголовок раздела встаёт вровень с первым пунктом
              рельса — раньше колонка и рельс начинались на разной высоте. */}
          {extraGroup && <ExtraGroupBlock group={extraGroup} />}

          {columnSection && (
            <SectionColumn
              section={columnSection}
              pathname={pathname}
              user={user}
              organizerPlace={columnSection.key === "organizer" ? organizerPlace : null}
            />
          )}

          {/* «Выйти» живёт в меню аккаунта в шапке; в колонке — только на
              страницах самого аккаунта (настройки, админка). */}
          {user != null && !hideSecondaryNav && columnSection?.key === "account" && (
            <LogoutButton className="site-col-logout" />
          )}
        </div>
      )}
    </aside>
  );
}

function SectionColumn({
  section,
  pathname,
  user,
  organizerPlace,
}: {
  section: NavSection;
  pathname: string;
  user: User | null | undefined;
  organizerPlace: NavPlace | null;
}) {
  if (section.key === "me" && user === null) {
    return (
      <div className="site-col-section">
        <div className="site-col-title">{section.label}</div>
        <p className="site-col-hint">
          Войдите, чтобы видеть свои пробежки, волонтёрства, достижения и карту локаций.
        </p>
        <a className="btn primary btn-sm site-col-login" href={PORTAL_LOGIN_HREF}>
          Войти
        </a>
      </div>
    );
  }
  return (
    <div className="site-col-section">
      {organizerPlace ? (
        <OrganizerSwitcher user={user} place={organizerPlace} variant="column" />
      ) : (
        <a className="site-col-title" href={section.href}>
          {section.label}
        </a>
      )}
      {section.groups.map((group) => (
        <div key={group.key} className={`site-col-group${group.context && section.key === "locations" ? " site-col-group-context" : ""}`}>
          {group.title && <div className="site-col-group-title">{group.title}</div>}
          {group.items.map((link) => {
            const isCurrent = isLinkCurrent(link, pathname);
            return (
              <a
                key={link.key}
                href={link.href}
                className={`site-col-item${isCurrent ? " active" : ""}${link.tone === "admin" ? " site-col-item-admin" : ""}`}
                aria-current={isCurrent ? "page" : undefined}
              >
                {link.icon && <span className="site-col-item-icon">{link.icon}</span>}
                <span className="site-col-item-label">{link.label}</span>
              </a>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function ExtraGroupBlock({ group }: { group: SidebarExtraGroup }) {
  const head = (
    <>
      <span className="site-col-extra-icon">
        {group.avatarUrl ? <img src={group.avatarUrl} alt="" /> : PROFILE_ICON}
      </span>
      <span className="site-col-extra-name">{group.title}</span>
    </>
  );
  return (
    <div className="site-col-section site-col-group-context">
      {group.onTitleClick ? (
        <button type="button" className="site-col-extra-head" onClick={group.onTitleClick}>
          {head}
        </button>
      ) : (
        <div className="site-col-extra-head">{head}</div>
      )}
      {group.items.map((item) => (
        <button
          key={item.key}
          type="button"
          onClick={item.onClick}
          className={`site-col-item${item.active ? " active" : ""}`}
          aria-current={item.active ? "page" : undefined}
        >
          {item.icon && <span className="site-col-item-icon">{item.icon}</span>}
          <span className="site-col-item-label">{item.label}</span>
        </button>
      ))}
    </div>
  );
}

export function LogoutButton({ className }: { className?: string }) {
  const handleLogout = async () => {
    try {
      await logout();
    } finally {
      clearCachedUser();
      window.location.href = PORTAL_LOGIN_HREF;
    }
  };
  return (
    <button type="button" className={className} onClick={() => void handleLogout()}>
      Выйти
    </button>
  );
}
