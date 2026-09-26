import { useEffect, type MouseEvent } from "react";
import { ThemeToggle } from "../../components/ThemeToggle";
import { PORTAL_HOME_HREF, PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { useOptionalUser } from "../../lib/useOptionalUser";
import { AccountMenu } from "./nav/AccountMenu";
import { SEARCH_ICON } from "./nav/navIcons";
import { resolveNavState } from "./nav/navState";
import { rememberOrganizerRole } from "./nav/organizerMemory";
import { useOrganizerLocations } from "./nav/OrganizerSwitcher";
import { SiteBottomNav } from "./nav/SiteBottomNav";
import { canSeeOrganizer } from "./nav/siteNav";
import { openSiteSearch } from "./nav/siteSearchBus";
import "./portal.css";
import "./nav/siteNav.css";

/**
 * Перейти к содержимому: фокус — на <main> страницы. Своего id у main нет
 * (каркасов много), поэтому ищем первый main в документе.
 */
function skipToContent(event: MouseEvent<HTMLAnchorElement>): void {
  const main = document.querySelector<HTMLElement>("main");
  if (!main) return;
  event.preventDefault();
  if (!main.hasAttribute("tabindex")) main.setAttribute("tabindex", "-1");
  main.focus();
}

/**
 * Шапка сайта: логотип, ссылки разделов, поиск, тема, аккаунт.
 *
 * Ссылки разделов собираются из того же дерева, что рельс и нижняя панель
 * (nav/siteNav.ts), с теми же словами и той же подсветкой, и видны только
 * на компьютере и только там, где рельса нет: главная, «О проекте», блог,
 * «Обновления», вход, онбординг, 404, админка (решение Дмитрия 26.09.2026).
 * Рядом с рельсом они были вторым меню разделов со своими названиями и
 * подсветкой, а шапка с ними вылезала за экран на ноутбуке и планшете.
 * Прячет их CSS по метке has-site-rail на <html> (её ставит SiteSidebar) —
 * каркасам страниц ничего передавать не нужно.
 *
 * Поиск — единственный вход в поиск на компьютере. Бургера на телефоне нет с
 * 23.09.2026: его роль играет единая нижняя панель с «Меню», и шапка рисует
 * её сама — так панель есть на любой странице с шапкой.
 */
export function PortalHeader({
  hideLogin = false,
  bottomNav = true,
}: {
  hideLogin?: boolean;
  /** Нижняя панель телефона; выключают страницы входа, где уходить некуда. */
  bottomNav?: boolean;
}) {
  // Кэшированная сессия (sessionStorage): при первом заходе и после F5 ник не
  // мигает кнопкой «Войти» — стартуем с последнего известного статуса.
  const optionalUser = useOptionalUser();
  const user = optionalUser ?? null;
  const authResolved = optionalUser !== undefined;

  // Роль организатора — в память браузера: следующая вкладка сразу нарисует
  // рельс и панель с «Оргкабинетом», не дожидаясь /auth/me.
  useEffect(() => {
    if (optionalUser !== undefined) rememberOrganizerRole(optionalUser);
  }, [optionalUser]);

  // Список своих локаций нужен, чтобы «Оргкабинет» вёл сразу в единственную
  // локацию. Хук общий на документ и ходит на сервер не чаще раза в 10
  // минут; у админа в списке весь каталог — ему его тут не грузим.
  useOrganizerLocations(optionalUser && canSeeOrganizer(optionalUser) && !optionalUser.is_admin ? optionalUser : null);

  const { sections, current } = resolveNavState({ user: optionalUser });
  // Те же разделы, что в рельсе, плюс «О проекте». Гостю «Войти» здесь не
  // дублируем — справа и так кнопка входа.
  const linkSections = sections.filter(
    (section) =>
      (section.inRail !== false || section.key === "project") && !(section.key === "me" && optionalUser === null),
  );

  return (
    <header className="portal-header">
      <a className="skip-link" href="#content" onClick={skipToContent}>
        Перейти к содержимому
      </a>
      <div className="portal-header-inner">
        <a href={PORTAL_HOME_HREF} className="portal-brand" aria-label="run5k.run — на главную">
          <span className="portal-brand-stack">
            <span className="portal-brand-row">
              <span className="portal-brand-name">
                run5k<span className="portal-brand-tld">.run</span>
              </span>
              <svg
                className="portal-brand-pulse"
                viewBox="0 0 34 14"
                fill="none"
                aria-hidden="true"
              >
                <polyline
                  points="1,12 8,10 14,11 20,6 26,7 32,2"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <circle cx="32" cy="2" r="2.4" />
              </svg>
            </span>
            <span className="portal-brand-tagline">Статистика парковых пробежек</span>
          </span>
        </a>
        <nav className="portal-header-nav" aria-label="Разделы сайта">
          {linkSections.map((section) => {
            const isCurrent = section.key === current?.key;
            return (
              <a
                key={section.key}
                href={section.href}
                className={`portal-header-link${isCurrent ? " portal-header-link-current" : ""}`}
                aria-current={isCurrent ? "true" : undefined}
              >
                {section.shortLabel}
              </a>
            );
          })}
        </nav>
        {/* Кнопка канала из шапки убрана (просьба Дмитрия 25.08.2026):
            ссылка на Telegram живёт в подвале. */}
        <div className="portal-header-actions">
          <button
            type="button"
            className="portal-header-search"
            onClick={() => openSiteSearch()}
            aria-label="Поиск по сайту"
            title="Поиск по сайту"
          >
            <span className="portal-header-search-icon">{SEARCH_ICON}</span>
            <span className="portal-header-search-label">Поиск</span>
            <kbd className="portal-header-search-kbd">/</kbd>
          </button>
          <ThemeToggle />
          {!hideLogin &&
            authResolved &&
            (user ? (
              // Имя открывает меню аккаунта: кабинет, настройки, админка,
              // выход (см. nav/AccountMenu).
              <AccountMenu user={user} />
            ) : (
              <a className="btn primary btn-sm portal-header-login" href={PORTAL_LOGIN_HREF}>
                Войти
              </a>
            ))}
        </div>
      </div>
      {bottomNav && <SiteBottomNav user={optionalUser} />}
    </header>
  );
}
