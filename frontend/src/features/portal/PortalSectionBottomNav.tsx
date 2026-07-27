import { useState } from "react";
import { logout } from "../../lib/api";
import { clearCachedUser, useOptionalUser } from "../../lib/useOptionalUser";
import { PORTAL_LOGIN_HREF, cabinetTabHref } from "../../lib/portalRoutes";
import { SECONDARY_NAV, SITE_SECTIONS_NAV, icon } from "./SiteSidebar";
import "./cabinet/cabinet.css";

/**
 * Нижняя навигация телефона для разделов сайта (Локации, Рейтинги, Бэклог).
 *
 * На мобильной ширине сайдбар скрыт, а своей нижней панели у разделов не было
 * вовсе: зайдя в «Локации», человек оставался без навигации совсем — вернуться
 * в кабинет можно было только через шапку. Панель повторяет кабинетную по
 * вёрстке (те же классы), но состав другой: возврат в кабинет, сами разделы и
 * «Ещё» со служебными пунктами.
 */
const MORE_ICON = icon(
  <>
    <circle cx="5" cy="12" r="1.6" />
    <circle cx="12" cy="12" r="1.6" />
    <circle cx="19" cy="12" r="1.6" />
  </>,
);

const CABINET_ICON = icon(
  <>
    <circle cx="12" cy="8" r="3.6" />
    <path d="M5 20c.8-3.6 3.6-5.7 7-5.7s6.2 2.1 7 5.7" />
  </>,
);

export function PortalSectionBottomNav({ active }: { active?: string | null }) {
  const user = useOptionalUser();
  const [moreOpen, setMoreOpen] = useState(false);

  // Гостю кнопка ведёт на вход — это и есть его путь в кабинет.
  const cabinetHref = user ? cabinetTabHref(user, "dashboard") : PORTAL_LOGIN_HREF;
  const cabinetLabel = user ? "Кабинет" : "Войти";

  const handleLogout = async () => {
    try {
      await logout();
    } finally {
      clearCachedUser();
      window.location.href = "/";
    }
  };

  return (
    <>
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
            {SECONDARY_NAV.filter(
              (item) =>
                (!item.adminOnly || user?.is_admin) && (!item.authOnly || user != null),
            ).map((item) => (
              <a
                key={item.href}
                href={item.href}
                className={`portal-cab-more-item portal-cab-more-item-secondary${
                  item.adminOnly ? " portal-cab-nav-item-admin" : ""
                }`}
              >
                {item.label}
              </a>
            ))}
            {user != null && (
              <button
                type="button"
                className="portal-cab-more-item portal-cab-more-item-secondary portal-cab-logout"
                onClick={() => void handleLogout()}
              >
                Выйти
              </button>
            )}
          </div>
        </div>
      )}

      <nav className="portal-cab-bottomnav" aria-label="Разделы сайта (телефон)">
        <a href={cabinetHref} className="portal-cab-bottomnav-item">
          <span className="portal-cab-nav-icon">{CABINET_ICON}</span>
          <span className="portal-cab-bottomnav-label">{cabinetLabel}</span>
        </a>
        {SITE_SECTIONS_NAV.map((item) => (
          <a
            key={item.href}
            href={item.href}
            className={`portal-cab-bottomnav-item${item.key === active ? " active" : ""}`}
            aria-current={item.key === active ? "page" : undefined}
          >
            <span className="portal-cab-nav-icon">{item.icon}</span>
            <span className="portal-cab-bottomnav-label">{item.label}</span>
          </a>
        ))}
        <button
          type="button"
          className={`portal-cab-bottomnav-item${moreOpen ? " active" : ""}`}
          aria-expanded={moreOpen}
          onClick={() => setMoreOpen((open) => !open)}
        >
          <span className="portal-cab-nav-icon">{MORE_ICON}</span>
          <span className="portal-cab-bottomnav-label">Ещё</span>
        </button>
      </nav>
    </>
  );
}
