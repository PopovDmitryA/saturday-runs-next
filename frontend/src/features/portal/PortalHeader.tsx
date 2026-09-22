import { ThemeToggle } from "../../components/ThemeToggle";
import {
  cabinetTabHref,
  PORTAL_ABOUT_HREF,
  PORTAL_HOME_HREF,
  PORTAL_LOGIN_HREF,
} from "../../lib/portalRoutes";
import { userLabel } from "../../lib/userLabel";
import { useOptionalUser } from "../../lib/useOptionalUser";
import { SEARCH_ICON } from "./nav/navIcons";
import { SiteBottomNav } from "./nav/SiteBottomNav";
import { openSiteSearch } from "./nav/siteSearchBus";

/**
 * Шапка сайта. С 23.09.2026 (вариант В навигации) бургера на телефоне нет:
 * его роль играет единая нижняя панель с «Меню», и шапка рисует её сама —
 * так панель есть на любой странице с шапкой, включая главную и блог.
 */
export function PortalHeader({
  hideLogin = false,
  bottomNav = true,
}: {
  hideLogin?: boolean;
  /** Нижняя панель телефона; выключают страницы входа, где уходить некуда. */
  bottomNav?: boolean;
}) {
  // Кэшированная сессия (sessionStorage): между переходами по MPA-страницам
  // ник не мигает кнопкой «Войти» — стартуем с последнего известного статуса.
  const optionalUser = useOptionalUser();
  const user = optionalUser ?? null;
  const authResolved = optionalUser !== undefined;

  const authed = user !== null;
  // Текущий раздел подсвечивается по адресу страницы: главная — точное
  // совпадение "/", остальные — по префиксу (напр. /about#privacy тоже
  // считается разделом «О проекте»). section — реальный раздел ссылки,
  // даже если аноним уходит на /login.
  const pathname = typeof window !== "undefined" ? window.location.pathname : "";
  const isCurrent = (section: string) =>
    section === PORTAL_HOME_HREF ? pathname === "/" : pathname.startsWith(section);
  const navLink = (section: string, href: string, label: string) => {
    const current = isCurrent(section);
    return (
      <a
        href={href}
        className={`portal-header-link${current ? " portal-header-link-current" : ""}`}
        aria-current={current ? "page" : undefined}
      >
        {label}
      </a>
    );
  };
  const navLinks = (
    <>
      {navLink(PORTAL_HOME_HREF, PORTAL_HOME_HREF, "Главная")}
      {/* «Личный кабинет» — всегда второй пункт, сразу после «Главной».
          Аноним уходит на вход, залогиненный — в новый кабинет (тёмный запуск
          /new/dashboard). Синяя кнопка «Войти» остаётся основным CTA. */}
      {navLink(
        "/dashboard",
        authed ? cabinetTabHref(user, "dashboard") : PORTAL_LOGIN_HREF,
        "Личный кабинет",
      )}
      {/* Локации и Рейтинги открыты без логина (25.07.2026) — аноним идёт
          прямо в разделы, личные блоки внутри зовут его войти сами. */}
      {navLink("/locations", "/locations", "Локации")}
      {/* «Результаты» — свой раздел с 25.08.2026: последние пробежки, единый
          протокол недели и журналы протоколов. */}
      {navLink("/results", "/results", "Результаты")}
      {navLink("/ratings", "/ratings", "Рейтинги")}
      {/* «О проекте» — последним пунктом; «Блог» из шапки убран по просьбе. */}
      {navLink(PORTAL_ABOUT_HREF, PORTAL_ABOUT_HREF, "О проекте")}
    </>
  );

  return (
    <header className="portal-header">
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
        <nav className="portal-header-nav" aria-label="Разделы портала">
          {navLinks}
        </nav>
        {/* Кнопка канала из шапки убрана (просьба Дмитрия 25.08.2026):
            ссылка на Telegram живёт в подвале, а шапка на телефоне и так
            переполнялась. */}
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
              <a
                className="portal-header-user"
                href={cabinetTabHref(user, "dashboard")}
                title="Личный кабинет"
              >
                {user.avatar_url && (
                  <img className="portal-header-user-avatar" src={user.avatar_url} alt="" />
                )}
                {userLabel(user)}
              </a>
            ) : (
              <a className="btn primary btn-sm" href={PORTAL_LOGIN_HREF}>
                Войти
              </a>
            ))}
        </div>
      </div>
      {bottomNav && <SiteBottomNav user={optionalUser} />}
    </header>
  );
}
