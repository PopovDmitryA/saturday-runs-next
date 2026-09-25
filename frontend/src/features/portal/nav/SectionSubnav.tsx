/**
 * Навигация по страницам раздела на телефоне — телефонная замена колонки
 * подразделов (вариант «гибрид», решение Дмитрия 23.09.2026).
 *
 * Первая версия рисовала страницы раздела кнопками-капсулами над контентом:
 * выглядело «не современно», уезжало вместе со страницей, а у рейтингов
 * капсулы шли в два ряда. Теперь полоса липнет под шапкой и бывает двух видов:
 * - до TABS_LIMIT страниц (локация, результаты, проект) — вкладки с
 *   подчёркиванием, как в Telegram и YouTube: соседние страницы видны сразу;
 * - больше (рейтинги, кабинет, организатор) — одна строка «Группа · Страница ▾»,
 *   по тапу прямо из неё выпадает список всех страниц раздела по группам.
 *   Четырнадцать вкладок в ряд — это половина их за краем экрана. Сначала
 *   список выезжал шторкой снизу, но палец жмёт вверху экрана и ждёт ответа
 *   там же (правка Дмитрия 25.09.2026).
 *
 * Пока полоса на странице, на <html> висит has-site-subnav: липкие шапки
 * таблиц и полоса «Кратко | Полно» отсчитывают свой верх от шапки сайта
 * вместе с этой полосой (см. siteNav.css), иначе полоса их закрывала бы.
 *
 * На компьютере полосы нет: там эту роль играет колонка SiteSidebar.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { User } from "../../../lib/api";
import { CHEVRON_DOWN_ICON } from "./navIcons";
import { resolveNavState, type NavStateInput } from "./navState";
import { OrganizerSwitcher } from "./OrganizerSwitcher";
import { isLinkCurrent, type NavGroup, type NavLink } from "./siteNav";

// Сколько страниц ещё помещаются вкладками. Локация (5) и результаты (2)
// видны целиком; кабинет (9), организатор (12) и рейтинги (13) — уже нет.
const TABS_LIMIT = 6;

function Tabs({ links, pathname }: { links: NavLink[]; pathname: string }) {
  const rowRef = useRef<HTMLDivElement>(null);
  // Прокручиваем сам ряд, а не страницу: scrollIntoView дёргал бы и
  // вертикальную прокрутку, если страницу открыли не с самого верха.
  useLayoutEffect(() => {
    const row = rowRef.current;
    const active = row?.querySelector<HTMLElement>(".site-tab.active");
    if (row && active) {
      row.scrollLeft = active.offsetLeft - (row.clientWidth - active.offsetWidth) / 2;
    }
  }, [pathname]);
  return (
    <div className="site-subnav-tabs" ref={rowRef}>
      {links.map((link) => {
        const isCurrent = isLinkCurrent(link, pathname);
        return (
          <a
            key={link.key}
            href={link.href}
            className={`site-tab${isCurrent ? " active" : ""}`}
            aria-current={isCurrent ? "page" : undefined}
          >
            {link.chipLabel ?? link.label}
          </a>
        );
      })}
    </div>
  );
}

function PickerDropdown({
  groups,
  pathname,
  onClose,
}: {
  groups: NavGroup[];
  pathname: string;
  onClose: () => void;
}) {
  // Пока список открыт, страница под ним не прокручивается; Esc закрывает.
  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previous;
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  return (
    <>
      {/* Затемнение под списком: тап мимо закрывает его. */}
      <div className="site-subnav-scrim" onClick={onClose} role="presentation" />
      <div className="site-subnav-dropdown" role="menu">
        {groups.map((group) => (
          <div key={group.key} className="site-subnav-dropdown-group">
            {group.title && <div className="site-subnav-dropdown-title">{group.title}</div>}
            {group.items.map((link) => {
              const isCurrent = isLinkCurrent(link, pathname);
              return (
                <a
                  key={link.key}
                  role="menuitem"
                  href={link.href}
                  className={`site-subnav-dropdown-item${isCurrent ? " active" : ""}`}
                  aria-current={isCurrent ? "page" : undefined}
                >
                  {link.icon && <span className="site-subnav-dropdown-icon">{link.icon}</span>}
                  {link.label}
                </a>
              );
            })}
          </div>
        ))}
      </div>
    </>
  );
}

export function SectionSubnav(props: Omit<NavStateInput, "user"> & { user: User | null | undefined }) {
  const { pathname, current, organizerPlace } = resolveNavState(props);
  const [menuOpen, setMenuOpen] = useState(false);
  const closeMenu = useCallback(() => setMenuOpen(false), []);

  // Какие страницы показывать: в кабинете организатора — его инструменты (на
  // «Моих локациях» полосы нет, там и так список), у открытой локации — её
  // страницы, в остальных разделах — раздел целиком. Каталог локаций своих
  // подстраниц не имеет.
  let groups: NavGroup[] = [];
  if (current && (current.key !== "organizer" || organizerPlace)) {
    const contextGroups = current.groups.filter((group) => group.context);
    groups = contextGroups.length > 0 ? contextGroups : current.groups;
  }
  const links = groups.flatMap((group) => group.items);
  const visible = links.length > 1;

  useEffect(() => {
    if (!visible) return;
    document.documentElement.classList.add("has-site-subnav");
    return () => document.documentElement.classList.remove("has-site-subnav");
  }, [visible]);

  if (!current || !visible) return null;

  const isOrganizer = current.key === "organizer" && organizerPlace;
  if (links.length <= TABS_LIMIT && !isOrganizer) {
    return (
      <nav className="site-subnav" aria-label={`Страницы раздела: ${current.label}`}>
        <Tabs links={links} pathname={pathname} />
      </nav>
    );
  }

  const found = groups
    .flatMap((group) => group.items.map((link) => ({ group, link })))
    .find(({ link }) => isLinkCurrent(link, pathname));
  const index = found ? links.indexOf(found.link) + 1 : 0;

  return (
    <nav className={`site-subnav site-subnav-switch${menuOpen ? " open" : ""}`} aria-label={`Страницы раздела: ${current.label}`}>
      {isOrganizer && (
        <div className="site-subnav-place">
          <OrganizerSwitcher user={props.user} place={organizerPlace} variant="chip" />
        </div>
      )}
      <button
        type="button"
        className="site-subnav-picker"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen((value) => !value)}
      >
        {found?.link.icon && <span className="site-subnav-picker-icon">{found.link.icon}</span>}
        <span className="site-subnav-picker-text">
          {found?.group.title && !isOrganizer && (
            <span className="site-subnav-picker-group">{found.group.title} · </span>
          )}
          <span className="site-subnav-picker-label">
            {/* У организатора полосу делят локация и инструмент — тут короткое имя. */}
            {found ? (isOrganizer ? (found.link.chipLabel ?? found.link.label) : found.link.label) : current.label}
          </span>
        </span>
        <span className="site-subnav-picker-chevron">{CHEVRON_DOWN_ICON}</span>
      </button>
      {index > 0 && (
        <span className="site-subnav-count" aria-label={`страница ${index} из ${links.length}`}>
          {index}/{links.length}
        </span>
      )}
      {menuOpen && <PickerDropdown groups={groups} pathname={pathname} onClose={closeMenu} />}
    </nav>
  );
}
