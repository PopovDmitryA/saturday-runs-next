import { useEffect, useState } from "react";
import { ThemeToggle } from "../../components/ThemeToggle";
import { getCurrentUser, type User } from "../../lib/api";
import {
  PORTAL_ABOUT_HREF,
  PORTAL_HOME_HREF,
  PORTAL_LOGIN_HREF,
} from "../../lib/portalRoutes";
import { userLabel } from "../../lib/userLabel";

export function PortalHeader({ hideLogin = false }: { hideLogin?: boolean }) {
  const [user, setUser] = useState<User | null>(null);
  // Пока авторизация не подтверждена, правый блок не рисуем вовсе: иначе
  // залогиненный на долю секунды видит «Войти», которое сменяется ником.
  const [authResolved, setAuthResolved] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    getCurrentUser()
      .then((current) => setUser(current))
      .catch(() => setUser(null))
      .finally(() => setAuthResolved(true));
  }, []);

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
      {/* Локации и Рейтинги под RequireAuth — анонима сразу ведём на вход,
          чтобы он не упирался в гейт внутри раздела. */}
      {navLink("/locations", authed ? "/locations" : PORTAL_LOGIN_HREF, "Локации")}
      {navLink("/ratings", authed ? "/ratings" : PORTAL_LOGIN_HREF, "Рейтинги")}
      {/* Явный пункт кабинета: аноним уходит на вход, залогиненный — в /dashboard.
          Синяя кнопка «Войти» остаётся как основной call-to-action. */}
      {navLink("/dashboard", authed ? "/dashboard" : PORTAL_LOGIN_HREF, "Личный кабинет")}
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
        <div className="portal-header-actions">
          <ThemeToggle />
          <a
            className="portal-header-channel"
            href="https://t.me/popov_way"
            target="_blank"
            rel="noreferrer"
            title="Канал автора в Telegram — о субботних пробежках, жизни и цифрах"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M21.5 2.7 2.6 10c-.9.35-.87 1.6.05 1.9l4.6 1.44 1.73 5.5c.27.86 1.36 1.06 1.92.36l2.05-2.53 4.5 3.3c.77.57 1.87.15 2.06-.79L22.9 4.1c.2-1-.55-1.72-1.4-1.4Z" />
            </svg>
            Канал
          </a>
          {!hideLogin &&
            authResolved &&
            (user ? (
              <a className="portal-header-user" href="/dashboard" title="Личный кабинет">
                {userLabel(user)}
              </a>
            ) : (
              <a className="btn primary btn-sm" href={PORTAL_LOGIN_HREF}>
                Войти
              </a>
            ))}
          <button
            type="button"
            className="portal-header-burger"
            aria-label={menuOpen ? "Закрыть меню" : "Открыть меню"}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              {menuOpen ? (
                <path d="M6 6l12 12M18 6L6 18" strokeWidth="2" strokeLinecap="round" />
              ) : (
                <path d="M4 7h16M4 12h16M4 17h16" strokeWidth="2" strokeLinecap="round" />
              )}
            </svg>
          </button>
        </div>
      </div>
      {menuOpen && (
        <nav className="portal-header-nav-mobile" aria-label="Разделы портала (мобильное меню)">
          {navLinks}
        </nav>
      )}
    </header>
  );
}
