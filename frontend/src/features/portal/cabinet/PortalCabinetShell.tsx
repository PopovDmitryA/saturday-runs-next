import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { logout, type User } from "../../../lib/api";
import { PORTAL_LOGIN_HREF } from "../../../lib/portalRoutes";
import { PortalHeader } from "../PortalHeader";
import { clearCachedUser } from "../../../lib/useOptionalUser";
import {
  CABINET_NAV,
  CabinetUserCard,
  SECONDARY_NAV,
  SITE_SECTIONS_NAV,
  SiteSidebar,
  icon,
  type CabinetTabKey,
} from "../SiteSidebar";
import "../portal.css";
import "./cabinet.css";

// Обратная совместимость: раньше эти сущности жили здесь.
export { userLabel, type CabinetTabKey } from "../SiteSidebar";

// Нижняя навигация телефона: 4 главных раздела + «Ещё» (остальное в шторке).
const BOTTOM_NAV_KEYS: CabinetTabKey[] = ["dashboard", "runs", "volunteering", "achievements"];

type PortalCabinetShellProps = {
  active: CabinetTabKey;
  user: User;
  // Заголовок страницы; на дашборде шапку рисует сам контент (герой).
  title?: string;
  sub?: string;
  // Подменить адреса разделов. Нужно превью на демо-данных: там навигация
  // ведёт на ?tab=…, иначе клик уходит на страницу под RequireAuth и без
  // сессии выбрасывает на вход.
  hrefForTab?: (key: CabinetTabKey, defaultHref: string) => string;
  // Превью: служебные пункты и «Выйти» скрыты — они уводят из демо-режима.
  hideSecondaryNav?: boolean;
  children: ReactNode;
};

// Модалки (DetailModal и т.п.) центрируются на весь viewport и не знают про
// сайдбар кабинета — из-за этого при сворачивании/разворачивании сайдбара
// модалка визуально "гуляла" относительно видимого контента. Кабинет пишет
// сюда фактические левый и правый края .portal-cab-main — ОБА, не только
// левый: на широких мониторах .portal-cab-layout сам центрируется с полями
// (max-width 1440), так что правый край контента тоже не совпадает с краем
// окна, и модалка вылезала за карточку с другой стороны, если считать одну
// только левую поправку. .modal-overlay в index.css добавляет оба значения
// к своим паддингам, так что центрирование считается строго между краями
// контентной колонки. На остальных страницах сайта переменные не
// выставляются — там модалки ведут себя как раньше.
const MODAL_CENTER_OFFSET_LEFT_VAR = "--modal-center-offset-left";
const MODAL_CENTER_OFFSET_RIGHT_VAR = "--modal-center-offset-right";

export function PortalCabinetShell({
  active,
  user,
  title,
  sub,
  hrefForTab,
  hideSecondaryNav = false,
  children,
}: PortalCabinetShellProps) {
  const tabHref = (item: (typeof CABINET_NAV)[number]) =>
    hrefForTab ? hrefForTab(item.key, item.href) : item.href;
  const [moreOpen, setMoreOpen] = useState(false);

  const mainRef = useRef<HTMLElement>(null);

  const measureModalOffset = () => {
    if (mainRef.current) {
      const rect = mainRef.current.getBoundingClientRect();
      const root = document.documentElement;
      root.style.setProperty(MODAL_CENTER_OFFSET_LEFT_VAR, `${rect.left}px`);
      root.style.setProperty(MODAL_CENTER_OFFSET_RIGHT_VAR, `${window.innerWidth - rect.right}px`);
    }
  };

  // Сайдбар сообщает о сворачивании после коммита DOM — пересчитываем офсет
  // модалок сразу (надёжнее ResizeObserver, который не срабатывал на смену
  // ширины из-за соседнего flex-элемента — см. историю в git).
  const handleCollapsedChange = useCallback(() => {
    measureModalOffset();
  }, []);

  // Пересчёт при ресайзе окна (адаптивный брейкпоинт сайдбара на 900px,
  // смена центрирующих полей страницы) и уборка переменной при
  // размонтировании кабинета — чтобы не протекала на остальные страницы.
  useEffect(() => {
    measureModalOffset();
    window.addEventListener("resize", measureModalOffset);
    // Ширина колонки меняется и без ресайза окна (догрузка данных, свёрнутый
    // сайдбар, смена вкладки). Без наблюдателя переменные оставались от
    // первого замера, и модалка-таблица открывалась узкой полосой.
    const observer = new ResizeObserver(() => measureModalOffset());
    if (mainRef.current) {
      observer.observe(mainRef.current);
    }
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measureModalOffset);
      const root = document.documentElement;
      root.style.removeProperty(MODAL_CENTER_OFFSET_LEFT_VAR);
      root.style.removeProperty(MODAL_CENTER_OFFSET_RIGHT_VAR);
    };
  }, []);

  const handleLogout = async () => {
    await logout();
    clearCachedUser();
    window.location.href = PORTAL_LOGIN_HREF;
  };

  const bottomItems = CABINET_NAV.filter((item) => BOTTOM_NAV_KEYS.includes(item.key));
  const moreItems = CABINET_NAV.filter((item) => !BOTTOM_NAV_KEYS.includes(item.key));
  const moreActive = moreItems.some((item) => item.key === active);

  return (
    <div className="portal-cab">
      {/* Та же шапка, что и на главной портала — с этого экрана вы уже
          авторизованы, так что навигация (Локации/Рейтинги/О проекте) и
          переход в кабинет по клику на ник работают идентично. */}
      <PortalHeader />

      <div className="portal-cab-layout">
        <SiteSidebar
          active={active}
          user={user}
          hrefForTab={hrefForTab}
          hideSecondaryNav={hideSecondaryNav}
          onCollapsedChange={handleCollapsedChange}
        />

        <main className="portal-cab-main" ref={mainRef}>
          {/* Телефон: сайдбар скрыт, вместе с ним пропадала и карточка
              участника — карандаш правки имени был доступен только с
              компьютера. Здесь та же карточка, видна только на узких
              экранах (см. .portal-cab-user-mobile). */}
          <div className="portal-cab-user-mobile">
            <CabinetUserCard initialUser={user} />
          </div>
          {title && (
            <div className="portal-cab-pagehead">
              <h1>{title}</h1>
              {sub && <p className="portal-cab-pagehead-sub">{sub}</p>}
            </div>
          )}
          {children}
        </main>
      </div>

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
            {moreItems.map((item) => (
              <a
                key={item.key}
                href={tabHref(item)}
                className={`portal-cab-more-item${item.key === active ? " active" : ""}`}
              >
                <span className="portal-cab-nav-icon">{item.icon}</span>
                {item.label}
              </a>
            ))}
            {/* Публичные разделы сайта: на десктопе они в сайдбаре, а на
                телефоне сайдбар скрыт — без них «Локации» и «Рейтинги» с
                телефона было не открыть вовсе. */}
            <div className="portal-cab-more-sep" />
            {SITE_SECTIONS_NAV.map((item) => (
              <a key={item.href} href={item.href} className="portal-cab-more-item">
                <span className="portal-cab-nav-icon">{item.icon}</span>
                {item.label}
              </a>
            ))}
            {!hideSecondaryNav && (
              <>
                <div className="portal-cab-more-sep" />
                {SECONDARY_NAV.filter((item) => !item.adminOnly || user.is_admin).map((item) => (
                  <a
                    key={item.href}
                    href={item.href}
                    className={`portal-cab-more-item portal-cab-more-item-secondary${item.adminOnly ? " portal-cab-nav-item-admin" : ""}`}
                  >
                    {item.label}
                  </a>
                ))}
                <button
                  type="button"
                  className="portal-cab-more-item portal-cab-more-item-secondary portal-cab-logout"
                  onClick={() => void handleLogout()}
                >
                  Выйти
                </button>
              </>
            )}
          </div>
        </div>
      )}

      <nav className="portal-cab-bottomnav" aria-label="Разделы личного кабинета (телефон)">
        {bottomItems.map((item) => (
          <a
            key={item.key}
            href={tabHref(item)}
            className={`portal-cab-bottomnav-item${item.key === active ? " active" : ""}`}
            aria-current={item.key === active ? "page" : undefined}
          >
            <span className="portal-cab-nav-icon">{item.icon}</span>
            <span className="portal-cab-bottomnav-label">{item.label}</span>
          </a>
        ))}
        <button
          type="button"
          className={`portal-cab-bottomnav-item${moreActive || moreOpen ? " active" : ""}`}
          aria-expanded={moreOpen}
          onClick={() => setMoreOpen((open) => !open)}
        >
          <span className="portal-cab-nav-icon">
            {icon(
              <>
                <circle cx="5" cy="12" r="1.6" />
                <circle cx="12" cy="12" r="1.6" />
                <circle cx="19" cy="12" r="1.6" />
              </>,
            )}
          </span>
          <span className="portal-cab-bottomnav-label">Ещё</span>
        </button>
      </nav>
    </div>
  );
}
