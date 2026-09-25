/**
 * Нижняя панель телефона — одна на весь сайт (вариант В, 23.09.2026).
 *
 * Раньше их было две с разным составом: у кабинета (Обзор, Пробежки,
 * Волонтёрство, Достижения, Ещё) и у разделов (Кабинет, Локации, Результаты,
 * Рейтинги, Ещё) — при переходе из кабинета в «Локации» панель менялась
 * целиком, и люди теряли ориентиры. Теперь панель живёт в шапке сайта и
 * одинакова на любой странице, включая главную, блог и «О проекте».
 *
 * Пять мест: Моё · Итоги · Локации · Рейтинги · Меню. У организаторов «Орг.»
 * встаёт вторым (кабинет организатора — самый посещаемый раздел сайта), а в
 * «Меню» уезжают «Итоги»: рейтинги открывают чаще (правка Дмитрия 25.09.2026;
 * хаб и таблицы рейтингов за месяц — около 2,9 тыс. просмотров против 1,1 тыс.
 * у последних пробежек и единого протокола).
 *
 * «Меню» — полная карта сайта из того же дерева, что рельс и колонка на
 * компьютере: если чего-то нет в полосе страниц раздела, оно точно есть здесь.
 */
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import type { User } from "../../../lib/api";
import { PORTAL_LOGIN_HREF } from "../../../lib/portalRoutes";
import { LogoutButton, SECTION_ICONS } from "../SiteSidebar";
import { CHEVRON_DOWN_ICON, MENU_ICON, SEARCH_ICON } from "./navIcons";
import { resolveNavState } from "./navState";
import { openSiteSearch } from "./siteSearchBus";
import { canSeeOrganizer, isLinkCurrent, type NavSectionKey } from "./siteNav";

const BASE_KEYS: NavSectionKey[] = ["me", "results", "locations", "ratings"];
const ORGANIZER_KEYS: NavSectionKey[] = ["me", "organizer", "locations", "ratings"];

export function SiteBottomNav({ user }: { user: User | null | undefined }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const { pathname, sections, current } = resolveNavState({ user });
  const keys = canSeeOrganizer(user) ? ORGANIZER_KEYS : BASE_KEYS;
  const barSections = keys
    .map((key) => sections.find((section) => section.key === key))
    .filter((section) => section != null);
  const currentInBar = barSections.some((section) => section.key === current?.key);
  const accountLinks =
    sections.find((section) => section.key === "account")?.groups.flatMap((group) => group.items) ?? [];

  // Пока шторка открыта, страница под ней не должна прокручиваться.
  useEffect(() => {
    if (!menuOpen) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previous;
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  // Отступ под панель нужен всем страницам сайта, а не только тем, где раньше
  // была своя панель: вешаем метку на <html>, отступ — в siteNav.css.
  useEffect(() => {
    document.documentElement.classList.add("has-site-bottomnav");
    return () => document.documentElement.classList.remove("has-site-bottomnav");
  }, []);

  if (typeof document === "undefined") return null;
  // Панель рисуется из шапки, а шапка — липкая со своим z-index: внутри неё
  // панель и шторка оказались бы под любым элементом страницы с z-index выше
  // шапки. Портал выносит их на уровень body.
  return createPortal(
    <>
      {menuOpen && (
        <div className="site-menu-backdrop" onClick={() => setMenuOpen(false)} role="presentation">
          <div
            className="site-menu-sheet"
            role="dialog"
            aria-label="Меню сайта"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="site-menu-grabber" aria-hidden="true" />
            <button
              type="button"
              className="site-menu-search"
              onClick={() => {
                setMenuOpen(false);
                openSiteSearch();
              }}
            >
              <span className="site-menu-search-icon">{SEARCH_ICON}</span>
              Найти страницу, локацию или участника
            </button>
            {/* Аккаунт — не раздел, а служебный блок внизу шторки. */}
            {sections.filter((section) => section.key !== "account").map((section) => (
              <MenuSection
                key={section.key}
                sectionKey={section.key}
                label={section.label}
                href={section.href}
                initiallyOpen={section.key === current?.key}
                groups={section.groups.filter((group) => !group.context)}
                pathname={pathname}
                anon={section.key === "me" && user === null}
              />
            ))}
            <div className="site-menu-footer">
              {accountLinks.map((link) => (
                <a
                  key={link.key}
                  href={link.href}
                  className={`site-menu-item${isLinkCurrent(link, pathname) ? " active" : ""}${
                    link.tone === "admin" ? " site-menu-item-admin" : ""
                  }`}
                >
                  {link.icon && <span className="site-menu-item-icon">{link.icon}</span>}
                  {link.label}
                </a>
              ))}
              {user != null ? (
                <LogoutButton className="site-menu-logout" />
              ) : user === null ? (
                <a className="btn primary site-menu-login" href={PORTAL_LOGIN_HREF}>
                  Войти
                </a>
              ) : null}
            </div>
          </div>
        </div>
      )}

      <nav className="site-bottomnav" aria-label="Разделы сайта (телефон)">
        {barSections.map((section) => {
          const isCurrent = section.key === current?.key;
          return (
            <a
              key={section.key}
              href={section.href}
              className={`site-bottomnav-item${isCurrent ? " active" : ""}`}
              aria-current={isCurrent ? "true" : undefined}
            >
              <span className="site-bottomnav-icon">{SECTION_ICONS[section.key]}</span>
              <span className="site-bottomnav-label">{section.shortLabel}</span>
            </a>
          );
        })}
        <button
          type="button"
          // Раздел, которого нет на панели (у организатора — «Рейтинги»,
          // у всех — «О проекте»), подсвечивает «Меню»: он живёт там.
          className={`site-bottomnav-item${menuOpen || (current && !currentInBar) ? " active" : ""}`}
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((open) => !open)}
        >
          <span className="site-bottomnav-icon">{MENU_ICON}</span>
          <span className="site-bottomnav-label">Меню</span>
        </button>
      </nav>
    </>,
    document.body,
  );
}

function MenuSection({
  sectionKey,
  label,
  href,
  initiallyOpen,
  groups,
  pathname,
  anon,
}: {
  sectionKey: NavSectionKey;
  label: string;
  href: string;
  initiallyOpen: boolean;
  groups: ReturnType<typeof resolveNavState>["sections"][number]["groups"];
  pathname: string;
  anon: boolean;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  const items = groups.flatMap((group) => group.items);
  // Раздел из одного пункта раскрывать незачем — это просто ссылка.
  if (anon || items.length <= 1) {
    return (
      <a className={`site-menu-head${initiallyOpen ? " current" : ""}`} href={items[0]?.href ?? href}>
        <span className="site-menu-head-icon">{SECTION_ICONS[sectionKey]}</span>
        <span className="site-menu-head-label">{anon ? "Войти в кабинет" : label}</span>
      </a>
    );
  }
  return (
    <div className={`site-menu-section${open ? " open" : ""}`}>
      <button
        type="button"
        className={`site-menu-head${initiallyOpen ? " current" : ""}`}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="site-menu-head-icon">{SECTION_ICONS[sectionKey]}</span>
        <span className="site-menu-head-label">{label}</span>
        <span className="site-menu-head-chevron">{CHEVRON_DOWN_ICON}</span>
      </button>
      {open && (
        <div className="site-menu-items">
          {groups.map((group) => (
            <div key={group.key} className="site-menu-group">
              {group.title && <div className="site-menu-group-title">{group.title}</div>}
              {group.items.map((link) => {
                const isCurrent = isLinkCurrent(link, pathname);
                return (
                  <a
                    key={link.key}
                    href={link.href}
                    className={`site-menu-item${isCurrent ? " active" : ""}${link.tone === "admin" ? " site-menu-item-admin" : ""}`}
                    aria-current={isCurrent ? "page" : undefined}
                  >
                    {link.icon && <span className="site-menu-item-icon">{link.icon}</span>}
                    {link.label}
                  </a>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
