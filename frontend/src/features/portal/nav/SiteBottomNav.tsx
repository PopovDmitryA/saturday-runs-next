/**
 * Нижняя панель телефона — одна на весь сайт (вариант В, 23.09.2026).
 *
 * Раньше их было две с разным составом: у кабинета (Обзор, Пробежки,
 * Волонтёрство, Достижения, Ещё) и у разделов (Кабинет, Локации, Результаты,
 * Рейтинги, Ещё) — при переходе из кабинета в «Локации» панель менялась
 * целиком, и люди теряли ориентиры. Теперь панель живёт в шапке сайта и
 * одинакова на любой странице, включая главную, блог и «О проекте».
 *
 * Пять мест: Кабинет · Итоги · Локации · Рейтинги · Меню. У организаторов
 * «Оргкабинет» встаёт вторым (кабинет организатора — самый посещаемый раздел
 * сайта), а в «Меню» уезжают «Итоги»: рейтинги открывают чаще (правка Дмитрия
 * 25.09.2026; хаб и таблицы рейтингов за месяц — около 2,9 тыс. просмотров
 * против 1,1 тыс. у последних пробежек и единого протокола).
 *
 * «Меню» — полная карта сайта из того же дерева, что рельс и колонка на
 * компьютере: если чего-то нет в полосе страниц раздела, оно точно есть здесь.
 *
 * Повторное нажатие на кнопку раздела, в котором человек уже находится,
 * открывает над панелью список страниц этого раздела (идея В, решение Дмитрия
 * 26.09.2026). Раньше у кабинета была своя нижняя панель, и «Пробежки» или
 * «Достижения» открывались одним касанием внизу; с полосой страниц сверху это
 * стало два касания у верхнего края, куда большой палец дотягивается хуже.
 * Полоса при этом остаётся: список внизу — второй путь, под пальцем.
 *
 * «Меню» и список раздела — окна поверх страницы: «Назад» их закрывает, не
 * уводя со страницы (nav/useOverlayHistory), фокус ходит внутри окна
 * (nav/useOverlayFocus). Сама панель остаётся над затемнением: «Меню» — это
 * переключатель, второе нажатие закрывает шторку.
 */
import { useCallback, useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { createPortal } from "react-dom";
import type { User } from "../../../lib/api";
import { LogoutButton, SECTION_ICONS } from "../SiteSidebar";
import { CHEVRON_DOWN_ICON, CLOSE_ICON, MENU_ICON, SEARCH_ICON } from "./navIcons";
import { resolveNavState } from "./navState";
import { openSiteSearch } from "./siteSearchBus";
import { canSeeOrganizer, isLinkCurrent, type NavGroup, type NavSection, type NavSectionKey } from "./siteNav";
import { useOverlayFocus } from "./useOverlayFocus";
import { useOverlayHistory } from "./useOverlayHistory";
import "./siteNavMobile.css";

const BASE_KEYS: NavSectionKey[] = ["me", "results", "locations", "ratings"];
const ORGANIZER_KEYS: NavSectionKey[] = ["me", "organizer", "locations", "ratings"];
/** Ширина, на которой есть нижняя панель (тот же порог, что в siteNav.css). */
const PHONE_QUERY = "(max-width: 900px)";

type Overlay = "menu" | "section" | null;

/** Страницы раздела для списка над панелью: все группы, где есть пункты. */
function sectionGroups(section: NavSection): NavGroup[] {
  return section.groups.filter((group) => group.items.length > 0);
}

function sectionLinkCount(section: NavSection): number {
  return sectionGroups(section).reduce((sum, group) => sum + group.items.length, 0);
}

function normalizedPath(path: string): string {
  return path.replace(/\/+$/, "") || "/";
}

export function SiteBottomNav({ user }: { user: User | null | undefined }) {
  const [overlay, setOverlay] = useState<Overlay>(null);
  const { pathname, sections, current, organizerPlace } = resolveNavState({ user });
  const keys = canSeeOrganizer(user) ? ORGANIZER_KEYS : BASE_KEYS;
  const barSections = keys
    .map((key) => sections.find((section) => section.key === key))
    .filter((section) => section != null);
  const currentInBar = barSections.some((section) => section.key === current?.key);
  const accountLinks =
    sections.find((section) => section.key === "account")?.groups.flatMap((group) => group.items) ?? [];

  const navRef = useRef<HTMLElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLElement | null>(null);

  const closeOverlay = useCallback(() => setOverlay(null), []);
  const overlayHistory = useOverlayHistory(overlay !== null, closeOverlay);

  useOverlayFocus({
    open: overlay !== null,
    // В порядке разметки: Tab с «Меню» на панели идёт в шторку, с последнего
    // пункта шторки — по кругу на панель.
    containers: [navRef, panelRef],
    trigger: triggerRef,
    initial: () => {
      const panel = panelRef.current;
      if (!panel) return null;
      // В «Меню» — сразу на поиск; в списке раздела — на текущую страницу.
      return (
        panel.querySelector<HTMLElement>(".site-menu-search") ??
        panel.querySelector<HTMLElement>("[aria-current='page']") ??
        panel.querySelector<HTMLElement>("a[href]")
      );
    },
    onEscape: overlayHistory.dismiss,
  });

  // Пока окно открыто, страница под ним не прокручивается и недоступна ни
  // пальцу, ни клавиатуре, ни экранному диктору (inert). Сама панель и окно
  // живут в портале вне #root — их это не касается.
  useEffect(() => {
    if (overlay === null) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const root = document.getElementById("root");
    root?.setAttribute("inert", "");
    return () => {
      document.body.style.overflow = previous;
      root?.removeAttribute("inert");
    };
  }, [overlay]);

  // Окно живёт только на телефоне: повернули планшет шире 900px — панели и
  // шторки там нет, а страница под inert осталась бы недоступной.
  useEffect(() => {
    if (overlay === null) return;
    const media = window.matchMedia(PHONE_QUERY);
    const onChange = () => {
      if (!media.matches) overlayHistory.dismiss();
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [overlay, overlayHistory.dismiss]);

  // В списке раздела текущая страница должна быть видна сразу, даже если
  // список длиннее экрана (рейтинги — 14 пунктов).
  useEffect(() => {
    if (overlay !== "section") return;
    const active = panelRef.current?.querySelector<HTMLElement>("[aria-current='page']");
    active?.scrollIntoView({ block: "nearest" });
  }, [overlay]);

  // Отступ под панель нужен всем страницам сайта, а не только тем, где раньше
  // была своя панель: вешаем метку на <html>, отступ — в siteNav.css.
  useEffect(() => {
    document.documentElement.classList.add("has-site-bottomnav");
    return () => document.documentElement.classList.remove("has-site-bottomnav");
  }, []);

  const toggle = (next: Exclude<Overlay, null>, event: ReactMouseEvent<HTMLElement>) => {
    if (overlay === next) {
      overlayHistory.dismiss();
      return;
    }
    // Из одного окна в другое — без лишней записи в истории: запись одна,
    // меняется только содержимое.
    triggerRef.current = event.currentTarget;
    setOverlay(next);
  };

  if (typeof document === "undefined") return null;

  const sectionLabel = (section: NavSection) => section.shortLabel;
  const panelSection = overlay === "section" ? current : null;

  // Панель рисуется из шапки, а шапка — липкая со своим z-index: внутри неё
  // панель и шторка оказались бы под любым элементом страницы с z-index выше
  // шапки. Портал выносит их на уровень body.
  return createPortal(
    <>
      {/* Панель — первой в разметке: экранный диктор читает «Меню», а сразу
          за ним — открытую шторку. Друг над другом их раскладывает z-index. */}
      <nav
        ref={navRef}
        className={`site-bottomnav${overlay !== null ? " over-scrim" : ""}`}
        aria-label="Разделы сайта"
        onClick={overlayHistory.interceptLinks}
      >
        {barSections.map((section) => {
          const isCurrent = section.key === current?.key;
          const content = (
            <>
              <span className="site-bottomnav-icon">{SECTION_ICONS[section.key]}</span>
              <span className="site-bottomnav-label">{sectionLabel(section)}</span>
            </>
          );
          // Кнопка текущего раздела со своими страницами — переключатель списка
          // над панелью (идея В). Первое нажатие на другой раздел — переход.
          if (isCurrent && sectionLinkCount(section) > 1) {
            const open = overlay === "section";
            return (
              <button
                key={section.key}
                type="button"
                className={`site-bottomnav-item active${open ? " open" : ""}`}
                aria-current="true"
                aria-haspopup="dialog"
                aria-expanded={open}
                title="Страницы раздела"
                onClick={(event) => toggle("section", event)}
              >
                {content}
              </button>
            );
          }
          return (
            <a
              key={section.key}
              href={section.href}
              className={`site-bottomnav-item${isCurrent ? " active" : ""}`}
              aria-current={isCurrent ? "true" : undefined}
              onClick={(event) => {
                // Уже на этой странице — наверх, а не перезагрузка той же страницы.
                if (overlay === null && normalizedPath(section.href) === normalizedPath(pathname)) {
                  event.preventDefault();
                  window.scrollTo({ top: 0, behavior: "smooth" });
                }
              }}
            >
              {content}
            </a>
          );
        })}
        <button
          type="button"
          // Раздел, которого нет на панели (у организатора — «Итоги», у всех —
          // «О проекте»), подсвечивает «Меню»: он живёт там.
          className={`site-bottomnav-item${
            overlay === "menu" || (overlay === null && current && !currentInBar) ? " active" : ""
          }${overlay === "menu" ? " open" : ""}`}
          aria-haspopup="dialog"
          aria-expanded={overlay === "menu"}
          onClick={(event) => toggle("menu", event)}
        >
          {/* Подпись не меняется (панель не должна «прыгать»), а крестик на
              месте значка подсказывает, что второе нажатие закроет шторку. */}
          <span className="site-bottomnav-icon">{overlay === "menu" ? CLOSE_ICON : MENU_ICON}</span>
          <span className="site-bottomnav-label">Меню</span>
        </button>
      </nav>

      {overlay !== null && <div className="site-menu-backdrop" onClick={overlayHistory.dismiss} role="presentation" />}

      {overlay === "menu" && (
        <div
          ref={panelRef}
          className="site-menu-sheet"
          role="dialog"
          aria-label="Меню сайта"
          onClick={overlayHistory.interceptLinks}
        >
          {/* Вместо «ручки», которая обещала смахивание, но не смахивалась, —
              честная кнопка «Закрыть». Закрывают шторку ещё «Назад», тап мимо
              и повторное нажатие «Меню». */}
          <div className="site-menu-top">
            <span className="site-menu-title">Меню</span>
            <button type="button" className="site-overlay-close" onClick={overlayHistory.dismiss}>
              <span className="site-overlay-close-icon">{CLOSE_ICON}</span>
              Закрыть
            </button>
          </div>
          <button
            type="button"
            className="site-menu-search"
            onClick={() => overlayHistory.dismissThen(() => openSiteSearch())}
          >
            <span className="site-menu-search-icon">{SEARCH_ICON}</span>
            Найти страницу, локацию или участника
          </button>
          {/* Аккаунт — не раздел, а служебный блок внизу шторки. */}
          {sections
            .filter((section) => section.key !== "account")
            .map((section) => (
              <MenuSection
                key={section.key}
                sectionKey={section.key}
                label={sectionLabel(section)}
                href={section.href}
                isCurrent={section.key === current?.key}
                groups={section.groups.filter((group) => !group.context)}
                pathname={pathname}
                anon={section.key === "me" && user === null}
              />
            ))}
          {/* Гостю вход — один раз, строкой «Войти» наверху списка; второй
              кнопки «Войти» внизу больше нет (mob-10). */}
          {(accountLinks.length > 0 || user != null) && (
            <div className="site-menu-footer">
              {accountLinks.map((link) => {
                const isCurrent = isLinkCurrent(link, pathname);
                return (
                  <a
                    key={link.key}
                    href={link.href}
                    className={`site-menu-item${isCurrent ? " active" : ""}${
                      link.tone === "admin" ? " site-menu-item-admin" : ""
                    }`}
                    aria-current={isCurrent ? "page" : undefined}
                  >
                    {link.icon && <span className="site-menu-item-icon">{link.icon}</span>}
                    {link.label}
                  </a>
                );
              })}
              {/* «Выйти» уводит на /login полной перезагрузкой — запись шторки
                  снимаем заранее, иначе после выхода «Назад» один раз вёл бы
                  «никуда». */}
              {user != null && (
                <div onClickCapture={overlayHistory.dismiss}>
                  <LogoutButton className="site-menu-logout" />
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {panelSection && (
        <div
          ref={panelRef}
          className="site-pages-panel"
          role="dialog"
          aria-label={`Страницы раздела «${sectionLabel(panelSection)}»`}
          onClick={overlayHistory.interceptLinks}
        >
          <div className="site-pages-head">
            <span className="site-pages-head-icon">{SECTION_ICONS[panelSection.key]}</span>
            <span className="site-pages-head-text">
              <span className="site-pages-head-label">{sectionLabel(panelSection)}</span>
              {/* Чей это кабинет — второй строкой: в одну с «Закрыть» имя
                  локации на 360px обрезалось до буквы. */}
              {panelSection.key === "organizer" && organizerPlace && (
                <span className="site-pages-head-place">{organizerPlace.name}</span>
              )}
            </span>
            <button type="button" className="site-overlay-close" onClick={overlayHistory.dismiss}>
              <span className="site-overlay-close-icon">{CLOSE_ICON}</span>
              Закрыть
            </button>
          </div>
          <div className="site-pages-list">
            {sectionGroups(panelSection).map((group) => (
              <div key={group.key} className="site-pages-group">
                {group.title && <div className="site-pages-group-title">{group.title}</div>}
                {group.items.map((link) => {
                  const isCurrent = isLinkCurrent(link, pathname);
                  return (
                    <a
                      key={link.key}
                      href={link.href}
                      className={`site-pages-item${isCurrent ? " active" : ""}`}
                      aria-current={isCurrent ? "page" : undefined}
                    >
                      {link.icon && <span className="site-pages-item-icon">{link.icon}</span>}
                      <span className="site-pages-item-label">{link.label}</span>
                    </a>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </>,
    document.body,
  );
}

function MenuSection({
  sectionKey,
  label,
  href,
  isCurrent,
  groups,
  pathname,
  anon,
}: {
  sectionKey: NavSectionKey;
  label: string;
  href: string;
  isCurrent: boolean;
  groups: NavGroup[];
  pathname: string;
  anon: boolean;
}) {
  const [open, setOpen] = useState(isCurrent);
  const items = groups.flatMap((group) => group.items);
  // Раздел из одного пункта раскрывать незачем — это просто ссылка. Гостю
  // «Кабинет» — это вход: подпись «Войти» приходит из дерева.
  if (anon || items.length <= 1) {
    const target = anon ? href : (items[0]?.href ?? href);
    const here = !anon && normalizedPath(target) === normalizedPath(pathname);
    return (
      <a
        className={`site-menu-head${isCurrent ? " current" : ""}`}
        href={target}
        aria-current={here ? "page" : undefined}
      >
        <span className="site-menu-head-icon">{SECTION_ICONS[sectionKey]}</span>
        <span className="site-menu-head-label">{label}</span>
      </a>
    );
  }
  return (
    <div className={`site-menu-section${open ? " open" : ""}`}>
      <button
        type="button"
        className={`site-menu-head${isCurrent ? " current" : ""}`}
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
                const linkCurrent = isLinkCurrent(link, pathname);
                return (
                  <a
                    key={link.key}
                    href={link.href}
                    className={`site-menu-item${linkCurrent ? " active" : ""}${link.tone === "admin" ? " site-menu-item-admin" : ""}`}
                    aria-current={linkCurrent ? "page" : undefined}
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
