/**
 * Навигация по страницам раздела на телефоне — телефонная замена колонки
 * подразделов (вариант «гибрид», решение Дмитрия 23.09.2026).
 *
 * Первая версия рисовала страницы раздела кнопками-капсулами над контентом:
 * выглядело «не современно», уезжало вместе со страницей, а у рейтингов
 * капсулы шли в два ряда. Теперь полоса липнет под шапкой и бывает двух видов:
 * - до TABS_LIMIT страниц (локация, итоги, проект) — вкладки с
 *   подчёркиванием, как в Telegram и YouTube: соседние страницы видны сразу;
 * - больше (рейтинги, кабинет, организатор, чужой профиль) — одна строка
 *   «Группа · Страница ▾», по тапу прямо из неё выпадает список всех страниц
 *   раздела по группам. Четырнадцать вкладок в ряд — это половина их за краем
 *   экрана. Сначала список выезжал шторкой снизу, но палец жмёт вверху экрана
 *   и ждёт ответа там же (правка Дмитрия 25.09.2026).
 *
 * Выпадающий список — окно поверх страницы: «Назад» закрывает его, не уводя со
 * страницы (nav/useOverlayHistory), фокус при открытии уходит в список, Tab не
 * выходит за полосу, Esc закрывает и возвращает фокус на кнопку. Разметка —
 * «раскрывашка» (кнопка с aria-expanded и обычный список ссылок), а не
 * role=menu: меню без стрелок сбивало экранных дикторов (ревью, a11y-2).
 * Пока список открыт, затемнение лежит и над шапкой, и над нижней панелью, и
 * над плашкой «вы» в рейтингах: раньше они оставались нажимаемыми поверх
 * затемнения, а плашка закрывала последний пункт списка (проверка 26.09.2026).
 *
 * Страницы берутся из дерева навигации (siteNav.ts) по текущему разделу. Чужой
 * профиль — не раздел сайта, его вкладки страница передаёт сама (`custom`):
 * так у своего кабинета и чужого профиля одна и та же полоса.
 *
 * Пока полоса на странице, на <html> висит has-site-subnav: липкие шапки
 * таблиц и полоса «Кратко | Полно» отсчитывают свой верх от шапки сайта
 * вместе с этой полосой (см. siteNav.css), иначе полоса их закрывала бы.
 * Эта же метка прячет хлебные крошки над заголовком страницы (полоса уже
 * говорит, где человек, см. siteNavMobile.css). Кроме кабинета организатора
 * (метка has-site-subnav-organizer): там в крошках единственная ссылка из
 * кабинета на публичную страницу локации, и полоса её не заменяет.
 *
 * На компьютере полосы нет: там эту роль играет колонка SiteSidebar.
 */
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { User } from "../../../lib/api";
import { lockBodyScroll } from "../../../lib/bodyScrollLock";
import { CHEVRON_DOWN_ICON } from "./navIcons";
import { resolveNavState, type NavStateInput } from "./navState";
import { OrganizerSwitcher } from "./OrganizerSwitcher";
import { isLinkCurrent, type NavGroup, type NavLink } from "./siteNav";
import { useOverlayFocus } from "./useOverlayFocus";
import { useOverlayHistory } from "./useOverlayHistory";
import "./siteNavMobile.css";

// Сколько страниц ещё помещаются вкладками. Локация (5) и итоги (2) видны
// целиком; кабинет (8), чужой профиль (7), организатор (12) и рейтинги (14) —
// уже нет.
const TABS_LIMIT = 6;

/** С какой стороны за краем ряда ещё есть вкладки — там край тает. */
function updateFade(row: HTMLElement): void {
  const max = row.scrollWidth - row.clientWidth;
  const left = row.scrollLeft > 1;
  const right = max - row.scrollLeft > 1;
  const fade = left && right ? "both" : left ? "left" : right ? "right" : "";
  if (fade) {
    row.dataset.fade = fade;
  } else {
    delete row.dataset.fade;
  }
}

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
    if (row) updateFade(row);
  }, [pathname]);
  // Ширина экрана меняется (поворот телефона) — пересчитываем затухание.
  useEffect(() => {
    const row = rowRef.current;
    if (!row) return;
    const observer = new ResizeObserver(() => updateFade(row));
    observer.observe(row);
    return () => observer.disconnect();
  }, []);
  return (
    <div className="site-subnav-tabs" ref={rowRef} onScroll={(event) => updateFade(event.currentTarget)}>
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
  id,
  groups,
  pathname,
  onDismiss,
}: {
  id: string;
  groups: NavGroup[];
  pathname: string;
  onDismiss: () => void;
}) {
  // Переходы по ссылкам списка (и по любым другим, пока он открыт) ловит
  // useOverlayHistory на всём документе: сначала снимает запись списка из
  // истории, потом переходит.
  return (
    <>
      {/* Затемнение — выше полосы (над шапкой) и ниже неё, до низа экрана;
          сама полоса остаётся яркой. Тап мимо закрывает список. */}
      <div className="site-subnav-scrim site-subnav-scrim-top" onClick={onDismiss} role="presentation" />
      <div className="site-subnav-scrim" onClick={onDismiss} role="presentation" />
      <div id={id} className="site-subnav-dropdown">
        {groups.map((group) => (
          <div
            key={group.key}
            className="site-subnav-dropdown-group"
            role={group.title ? "group" : undefined}
            aria-label={group.title}
          >
            {group.title && (
              <div className="site-subnav-dropdown-title" aria-hidden="true">
                {group.title}
              </div>
            )}
            {group.items.map((link) => {
              const isCurrent = isLinkCurrent(link, pathname);
              return (
                <a
                  key={link.key}
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

export type SectionSubnavCustom = {
  /** Как назвать раздел для экранного диктора: «Страницы раздела: …». */
  label: string;
  groups: NavGroup[];
};

export function SectionSubnav(
  props: Omit<NavStateInput, "user"> & {
    user: User | null | undefined;
    /**
     * Свои страницы вместо раздела из дерева — у чужого профиля, который
     * разделом сайта не является. Текущую вкладку ссылки отмечают сами
     * (NavLink.matches).
     */
    custom?: SectionSubnavCustom;
  },
) {
  const { pathname, current, organizerPlace } = resolveNavState(props);
  const { custom } = props;
  const [menuOpen, setMenuOpen] = useState(false);
  const closeMenu = useCallback(() => setMenuOpen(false), []);
  const overlayHistory = useOverlayHistory(menuOpen, closeMenu);
  const navRef = useRef<HTMLElement>(null);
  const pickerRef = useRef<HTMLButtonElement>(null);
  const dropdownId = useId();

  // Какие страницы показывать: в кабинете организатора — его инструменты (на
  // «Моих локациях» полосы нет, там и так список), у открытой локации — её
  // страницы, в остальных разделах — раздел целиком. У каталога локаций
  // подстраниц нет: «Моя локация» и недавние в колонке — это ярлыки на другие
  // локации, а не страницы каталога; на телефоне они в списке над кнопкой
  // «Локации» (повторное нажатие) и в «Меню».
  let groups: NavGroup[] = [];
  if (custom) {
    groups = custom.groups;
  } else if (current && (current.key !== "organizer" || organizerPlace)) {
    const contextGroups = current.groups.filter((group) => group.context);
    if (contextGroups.length > 0) {
      groups = contextGroups;
    } else if (current.key !== "locations") {
      groups = current.groups;
    }
  }
  const links = groups.flatMap((group) => group.items);
  const visible = links.length > 1;
  const sectionLabel = custom?.label ?? current?.label ?? "";

  useEffect(() => {
    if (!visible) return;
    document.documentElement.classList.add("has-site-subnav");
    return () => document.documentElement.classList.remove("has-site-subnav");
  }, [visible]);

  const isOrganizer = !custom && current?.key === "organizer" && organizerPlace != null;
  useEffect(() => {
    if (!visible || !isOrganizer) return;
    document.documentElement.classList.add("has-site-subnav-organizer");
    return () => document.documentElement.classList.remove("has-site-subnav-organizer");
  }, [visible, isOrganizer]);

  // Пока список открыт, страница под ним не прокручивается (общая блокировка
  // со счётчиком, lib/bodyScrollLock: поиск, открытый поверх списка, забирает
  // его запись в истории, и список закрывается уже после открытия поиска).
  // Экран стал шире 900px (повернули планшет) — полосы там нет, закрываем и
  // список.
  const { dismiss } = overlayHistory;
  useEffect(() => {
    if (!menuOpen) return;
    const unlock = lockBodyScroll();
    const media = window.matchMedia("(max-width: 900px)");
    const onChange = () => {
      if (!media.matches) dismiss();
    };
    media.addEventListener("change", onChange);
    return () => {
      unlock();
      media.removeEventListener("change", onChange);
    };
  }, [menuOpen, dismiss]);

  useOverlayFocus({
    open: menuOpen,
    containers: [navRef],
    trigger: pickerRef,
    initial: () => {
      const list = document.getElementById(dropdownId);
      return list?.querySelector<HTMLElement>("[aria-current='page']") ?? list?.querySelector<HTMLElement>("a[href]") ?? null;
    },
    onEscape: overlayHistory.dismiss,
  });

  if ((!custom && !current) || !visible) return null;

  if (links.length <= TABS_LIMIT && !isOrganizer) {
    return (
      <nav className="site-subnav" aria-label={`Страницы раздела: ${sectionLabel}`}>
        <Tabs links={links} pathname={pathname} />
      </nav>
    );
  }

  const found = groups
    .flatMap((group) => group.items.map((link) => ({ group, link })))
    .find(({ link }) => isLinkCurrent(link, pathname));
  const index = found ? links.indexOf(found.link) + 1 : 0;

  return (
    <nav
      ref={navRef}
      className={`site-subnav site-subnav-switch${menuOpen ? " open" : ""}`}
      aria-label={`Страницы раздела: ${sectionLabel}`}
    >
      {isOrganizer && (
        <div className="site-subnav-place">
          <OrganizerSwitcher user={props.user} place={organizerPlace} variant="chip" />
        </div>
      )}
      <button
        ref={pickerRef}
        type="button"
        className="site-subnav-picker"
        aria-expanded={menuOpen}
        aria-controls={menuOpen ? dropdownId : undefined}
        onClick={() => (menuOpen ? overlayHistory.dismiss() : setMenuOpen(true))}
      >
        {found?.link.icon && <span className="site-subnav-picker-icon">{found.link.icon}</span>}
        <span className="site-subnav-picker-text">
          {found?.group.title && !isOrganizer && (
            <span className="site-subnav-picker-group">{found.group.title} · </span>
          )}
          <span className="site-subnav-picker-label">
            {/* У организатора полосу делят локация и инструмент — тут короткое имя.
                После группы («Бегуны · …») — та же короткая подпись, что в колонке
                на компьютере: полное «Количество пробежек» не влезало в 390px, а
                «Локации · Локации по регионам» повторяло слово. Полное имя — в H1. */}
            {found
              ? isOrganizer
                ? (found.link.chipLabel ?? found.link.label)
                : found.group.title
                  ? (found.link.colLabel ?? found.link.label)
                  : found.link.label
              : sectionLabel}
          </span>
        </span>
        <span className="site-subnav-picker-chevron">{CHEVRON_DOWN_ICON}</span>
      </button>
      {index > 0 && (
        <span className="site-subnav-count" aria-label={`страница ${index} из ${links.length}`}>
          {index}/{links.length}
        </span>
      )}
      {menuOpen && (
        <PickerDropdown
          id={dropdownId}
          groups={groups}
          pathname={pathname}
          onDismiss={overlayHistory.dismiss}
        />
      )}
    </nav>
  );
}
