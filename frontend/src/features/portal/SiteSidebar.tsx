/**
 * Навигация сайта на компьютере — вариант В (решение Дмитрия 23.09.2026):
 * узкий рельс разделов с иконками и колонка со ВСЕМИ страницами текущего
 * раздела. Раньше был один длинный сайдбар, где подпункты появлялись и
 * исчезали, 14 рейтингов прятались за одним пунктом, а кабинет организатора
 * жил подпунктом «Локаций».
 *
 * Состав пунктов — не здесь, а в nav/siteNav.ts: оттуда же рисуются нижняя
 * панель телефона, полоса страниц раздела, шторка «Меню», ссылки шапки,
 * подвал и поиск по страницам.
 *
 * Пока рельс на странице, на <html> висит has-site-rail: шапка по нему прячет
 * свои ссылки разделов — два меню одного уровня на одном экране путали, а
 * шапка с ними не влезала в ноутбук (решение Дмитрия 26.09.2026). Свёрнутая
 * колонка ставит site-col-collapsed: тогда над контентом появляется та же
 * полоса страниц раздела, что на телефоне (см. nav/siteNavDesktop.css), —
 * раньше в свёрнутом виде страницы раздела пропадали совсем.
 *
 * Интерфейс компонента прежний (active, location, user, extraGroup…), поэтому
 * три десятка страниц менять не пришлось.
 */
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { logout, type User } from "../../lib/api";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { userLabel } from "../../lib/userLabel";
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
  SETTINGS_ICON,
} from "./nav/navIcons";
import { resolveNavState, type SiteSidebarActive } from "./nav/navState";
import { forgetOrganizer, rememberOrganizerPlace } from "./nav/organizerMemory";
import { OrganizerSwitcher, useOrganizerLocations } from "./nav/OrganizerSwitcher";
import { canSeeOrganizer, isLinkCurrent, type NavSection, type NavSectionKey, type NavPlace } from "./nav/siteNav";
import "./cabinet/cabinet.css";
import "./nav/siteNav.css";
import "./nav/siteNavDesktop.css";

export { icon } from "./nav/navIcons";
export { isCabinetTab, type SiteSidebarActive } from "./nav/navState";
export type { CabinetTabKey } from "./nav/siteNav";
// Имя пользователя нужно и герою дашборда; живёт в lib/userLabel, отсюда —
// ради старых импортов.
export { userLabel };

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

// Свёрнута ли колонка (остаётся только рельс) — помнится между страницами:
// широким таблицам протоколов и рейтингов место нужнее, чем меню. Страницы
// раздела при этом не пропадают: их показывает полоса над контентом.
const COLUMN_COLLAPSED_KEY = "portalCabSidebarCollapsed";

// Метки на <html> ставят несколько компонентов (и старый экземпляр при
// переходе снимает свою уже после того, как новый поставил) — считаем, сколько
// раз метка нужна, и снимаем её только за последним.
const htmlClassCounts = new Map<string, number>();

function useHtmlClass(name: string, on: boolean): void {
  // До отрисовки: иначе шапка на кадр показала бы ссылки разделов рядом с рельсом.
  useLayoutEffect(() => {
    if (!on) return;
    const root = document.documentElement;
    htmlClassCounts.set(name, (htmlClassCounts.get(name) ?? 0) + 1);
    root.classList.add(name);
    return () => {
      const left = (htmlClassCounts.get(name) ?? 1) - 1;
      htmlClassCounts.set(name, left);
      if (left <= 0) root.classList.remove(name);
    };
  }, [name, on]);
}

export type SidebarExtraGroup = {
  /** Заголовок группы (например, имя участника на публичном профиле). */
  title: string;
  /** Аватарка участника — вместо родовой иконки профиля в заголовке группы. */
  avatarUrl?: string | null;
  /** Клик по заголовку: на публичном профиле имя ведёт на его главную. */
  onTitleClick?: () => void;
  /**
   * Пункты группы. С адресом (`href`) пункт — ссылка: открывается в новой
   * вкладке, а диктор читает его как навигацию, а не как кнопку. Без адреса —
   * кнопка с `onClick`.
   */
  items: { key: string; label: string; icon?: ReactNode; active: boolean; href?: string; onClick?: () => void }[];
};

export type SiteSidebarProps = {
  active: SiteSidebarActive;
  /** User — залогинен, null — аноним, undefined — определить самостоятельно. */
  user?: User | null;
  /** Открытая локация (или локация кабинета организатора). */
  location?: NavPlace;
  /** Сообщить контейнеру о сворачивании (пересчёт офсета модалок в ЛК). */
  onCollapsedChange?: (collapsed: boolean) => void;
  /** Доп. группа вкладок текущей страницы (публичный профиль участника). */
  extraGroup?: SidebarExtraGroup;
};

export function SiteSidebar({ active, user: userProp, location, onCollapsedChange, extraGroup }: SiteSidebarProps) {
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

  useHtmlClass("has-site-rail", true);
  useHtmlClass("site-col-collapsed", collapsed);

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

  const { pathname, sections, current, organizerPlace } = resolveNavState({ active, user, location });

  // Список локаций организатора: ради имени открытой локации и ради того,
  // чтобы «Оргкабинет» в рельсе перерисовался, когда список приедет (при
  // одной локации он ведёт сразу в неё). Админу он нужен только в самом
  // кабинете: у него это весь каталог.
  const inOrganizer = current?.key === "organizer";
  const organizerItems = useOrganizerLocations(
    user && canSeeOrganizer(user) && (inOrganizer || !user.is_admin) ? user : null,
  );

  // Открыл кабинет локации — «Оргкабинет» дальше ведёт прямо сюда.
  const userId = user?.id ?? null;
  const placeSlug = organizerPlace?.slug ?? null;
  const placeName =
    (placeSlug && organizerItems?.find((item) => item.slug === placeSlug)?.name) || organizerPlace?.name || null;
  useEffect(() => {
    if (userId && placeSlug) {
      rememberOrganizerPlace(userId, { slug: placeSlug, name: placeName ?? placeSlug });
    }
  }, [userId, placeSlug, placeName]);

  // В рельсе — только места, куда ходят за статистикой: «О проекте» и
  // аккаунт живут в шапке (см. inRail в siteNav.ts). Поиска в рельсе нет с
  // 26.09.2026: он один на сайт — в шапке.
  const visibleSections = sections.filter((section) => section.inRail !== false);
  // Колонка чужого профиля показывает его вкладки; иначе — текущий раздел, а
  // если раздела нет — свой кабинет.
  const columnSection = current ?? (extraGroup ? null : sections[0]);

  const colRef = useRef<HTMLElement>(null);
  useColumnScroll(colRef, `${pathname}|${collapsed}|${columnSection?.key ?? ""}`);

  const collapseLabel = collapsed ? "Показать меню" : "Свернуть меню";

  return (
    <aside className={`site-nav${collapsed ? " site-nav-collapsed" : ""}`} aria-label="Навигация по сайту">
      <nav className="site-rail" aria-label="Разделы сайта">
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
          aria-expanded={!collapsed}
          title={collapsed ? "Показать меню раздела" : "Свернуть меню — больше места таблицам"}
        >
          <span className="site-rail-icon">{collapsed ? CHEVRON_RIGHT_ICON : CHEVRON_LEFT_ICON}</span>
          <span className="site-rail-label site-rail-collapse-label">{collapseLabel}</span>
        </button>
      </nav>

      {/* Свёрнутая колонка не рисуется: страницы раздела (и вкладки чужого
          профиля) показывает полоса SectionSubnav над контентом. */}
      {!collapsed && (
        <nav
          className="site-col"
          ref={colRef}
          aria-label={columnSection ? `Страницы раздела «${columnSection.label}»` : `Страницы: ${extraGroup?.title ?? ""}`}
        >
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
          {user != null && columnSection?.key === "account" && <LogoutButton className="site-col-logout" />}
        </nav>
      )}
    </aside>
  );
}

/**
 * Колонка на невысоком экране: при открытии страницы прокручиваем её к
 * текущему пункту — сама по себе она стояла в начале, и «Регионы» или «Мы и
 * соседи» на ноутбуке 1366×768 оказывались ниже края (desk-8). Прокручиваем
 * только колонку, не страницу: scrollIntoView сдвинул бы и окно. Пока ниже
 * есть пункты, внизу колонки — затухание (класс site-col-more).
 */
function useColumnScroll(colRef: RefObject<HTMLElement | null>, trigger: string): void {
  useLayoutEffect(() => {
    const col = colRef.current;
    if (!col) return;
    const active = col.querySelector<HTMLElement>('[aria-current="page"]');
    if (active) {
      const box = col.getBoundingClientRect();
      const item = active.getBoundingClientRect();
      // Снизу оставляем место под затухание, чтобы пункт не тонул в нём.
      if (item.bottom > box.bottom - 28) {
        col.scrollTop += item.bottom - box.bottom + 40;
      } else if (item.top < box.top) {
        col.scrollTop -= box.top - item.top + 8;
      }
    }
    const update = () => {
      col.classList.toggle("site-col-more", col.scrollHeight - col.scrollTop - col.clientHeight > 2);
    };
    update();
    col.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(update) : null;
    observer?.observe(col);
    return () => {
      col.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      observer?.disconnect();
    };
  }, [colRef, trigger]);
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
        <h2 className="site-col-title">{section.label}</h2>
        <p className="site-col-hint">
          Войдите, чтобы видеть свои пробежки, волонтёрства, достижения и карту локаций.
        </p>
        <a className="btn primary btn-sm site-col-login" href={PORTAL_LOGIN_HREF}>
          Войти
        </a>
      </div>
    );
  }
  // Заголовок раздела — ссылка, только если на его адрес не ведёт ни один
  // пункт ниже: иначе клавиатура проходила бы одну и ту же страницу дважды
  // («Кабинет» и «Обзор», «Рейтинги» и «Все рейтинги»).
  const titleIsItem = section.groups.some((group) => group.items.some((link) => link.href === section.href));
  return (
    <div className="site-col-section">
      {organizerPlace ? (
        <OrganizerSwitcher user={user} place={organizerPlace} variant="column" />
      ) : titleIsItem ? (
        <h2 className="site-col-title">{section.label}</h2>
      ) : (
        <h2 className="site-col-title">
          <a href={section.href}>{section.label}</a>
        </h2>
      )}
      {section.groups.map((group) => (
        <div key={group.key} className={`site-col-group${group.context && section.key === "locations" ? " site-col-group-context" : ""}`}>
          {group.title && <div className="site-col-group-title">{group.title}</div>}
          {group.items.map((link) => {
            const isCurrent = isLinkCurrent(link, pathname);
            const text = link.colLabel ?? link.label;
            return (
              <a
                key={link.key}
                href={link.href}
                className={`site-col-item${isCurrent ? " active" : ""}${link.tone === "admin" ? " site-col-item-admin" : ""}`}
                aria-current={isCurrent ? "page" : undefined}
                // Короткая подпись — полное имя во всплывающей подсказке.
                title={text !== link.label ? link.label : undefined}
              >
                {link.icon && <span className="site-col-item-icon">{link.icon}</span>}
                <span className="site-col-item-label">{text}</span>
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
      {group.items.map((item) => {
        const className = `site-col-item${item.active ? " active" : ""}`;
        const body = (
          <>
            {item.icon && <span className="site-col-item-icon">{item.icon}</span>}
            <span className="site-col-item-label">{item.label}</span>
          </>
        );
        return item.href ? (
          <a
            key={item.key}
            href={item.href}
            onClick={item.onClick}
            className={className}
            aria-current={item.active ? "page" : undefined}
          >
            {body}
          </a>
        ) : (
          <button
            key={item.key}
            type="button"
            onClick={item.onClick}
            className={className}
            aria-current={item.active ? "page" : undefined}
          >
            {body}
          </button>
        );
      })}
    </div>
  );
}

export function LogoutButton({ className }: { className?: string }) {
  const handleLogout = async () => {
    try {
      await logout();
    } finally {
      clearCachedUser();
      // Роль и локации организатора — тоже личное: следующему, кто войдёт в
      // этом браузере, «Оргкабинет» чужого не покажет.
      forgetOrganizer();
      window.location.href = PORTAL_LOGIN_HREF;
    }
  };
  return (
    <button type="button" className={className} onClick={() => void handleLogout()}>
      Выйти
    </button>
  );
}
