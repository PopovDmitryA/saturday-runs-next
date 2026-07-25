import type { ReactNode } from "react";
import { PortalHeader } from "./PortalHeader";
import { PORTAL_CABINET_HREF, PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { useOptionalUser } from "../../lib/useOptionalUser";
import "./portal.css";
import "./portalSection.css";

/**
 * Каркас для разделов портала верхнего уровня (Локации, Рейтинги) — тех, что
 * доступны из шапки, но не являются вкладками личного кабинета (у тех свой
 * PortalCabinetShell с сайдбаром). Общая шапка `<PortalHeader/>` + контейнер
 * 1440px; опционально — левый сайдбар раздела (проп `sidebar`, ≤900px скрыт).
 *
 * Легаси-контент страниц (.card, .data-table, .lb-*, .loc-*) живёт внутри
 * `.portal-section` и рескинится scoped-правилами в portalSection.css — сами
 * компоненты страниц менять не нужно, только заменить их старый шелл на этот.
 */
export function PortalSectionShell({
  children,
  sidebar,
}: {
  children: ReactNode;
  sidebar?: ReactNode;
}) {
  if (!sidebar) {
    return (
      <div className="portal-section-page">
        <PortalHeader />
        <main className="portal-section">{children}</main>
      </div>
    );
  }
  return (
    <div className="portal-section-page">
      <PortalHeader />
      <div className="portal-section portal-section-with-sidebar">
        <aside className="portal-section-sidebar">{sidebar}</aside>
        <main className="portal-section-content">{children}</main>
      </div>
    </div>
  );
}

export type SectionNavItem = {
  href: string;
  label: string;
  /** Требует логина: анониму пункт не показывается (решение Дмитрия 25.07.2026 —
      у анонима в сайдбаре только доступное без логина + кнопка «Войти»). */
  authOnly?: boolean;
  /** Точное совпадение адреса для подсветки (иначе по префиксу). */
  exact?: boolean;
};

/**
 * Навигация раздела для сайдбара PortalSectionShell: список ссылок с
 * подсветкой текущей + нижний блок по состоянию сессии (анониму — «Войти»,
 * залогиненному — ссылка в личный кабинет).
 */
export function SectionSidebarNav({ title, items }: { title: string; items: SectionNavItem[] }) {
  const user = useOptionalUser();
  const pathname = typeof window !== "undefined" ? window.location.pathname : "";
  const isCurrent = (item: SectionNavItem) =>
    item.exact ? pathname === item.href : pathname === item.href || pathname.startsWith(`${item.href}/`);

  return (
    <nav className="portal-section-sidenav" aria-label={`Разделы: ${title}`}>
      <div className="portal-section-sidenav-title">{title}</div>
      {items
        .filter((item) => !item.authOnly || (user !== null && user !== undefined))
        .map((item) => (
          <a
            key={item.href}
            href={item.href}
            className={`portal-section-sidenav-item${isCurrent(item) ? " portal-section-sidenav-item-current" : ""}`}
            aria-current={isCurrent(item) ? "page" : undefined}
          >
            {item.label}
          </a>
        ))}
      {user === null && (
        <div className="portal-section-sidenav-foot">
          <p className="portal-section-sidenav-hint">Войдите, чтобы видеть здесь свою статистику.</p>
          <a className="btn primary btn-sm" href={PORTAL_LOGIN_HREF}>
            Войти
          </a>
        </div>
      )}
      {user != null && (
        <div className="portal-section-sidenav-foot">
          <a className="portal-section-sidenav-item" href={PORTAL_CABINET_HREF}>
            → Личный кабинет
          </a>
        </div>
      )}
    </nav>
  );
}
