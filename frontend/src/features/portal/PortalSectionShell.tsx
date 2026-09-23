import type { ReactNode } from "react";
import { PortalFooter } from "./PortalFooter";
import { PortalHeader } from "./PortalHeader";
import { SiteSidebar, type SiteSidebarProps } from "./SiteSidebar";
import { SectionSubnav } from "./nav/SectionSubnav";
import { useOptionalUser } from "../../lib/useOptionalUser";
import "./portal.css";
import "./portalSection.css";

/**
 * Каркас для разделов портала верхнего уровня (Локации, Рейтинги, Бэклог,
 * публичный профиль). Общая шапка `<PortalHeader/>` + контейнер 1440px.
 *
 * С пропом `sidebar` рендерится навигация сайта (SiteSidebar: рельс разделов
 * и колонка подразделов) в том же лейауте, что и личный кабинет
 * (.portal-cab-layout), а на телефоне — липкая полоса страниц раздела.
 * Нижнюю панель телефона рисует сама шапка. Без пропа — просто
 * центрированный контейнер.
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
  /** Параметры единого сайдбара; не передан — страница без сайдбара. */
  sidebar?: Pick<SiteSidebarProps, "active" | "location">;
}) {
  const user = useOptionalUser();
  if (!sidebar) {
    return (
      <div className="portal-section-page">
        <PortalHeader />
        <main className="portal-section">{children}</main>
        <PortalFooter />
      </div>
    );
  }
  return (
    <div className="portal-section-page">
      <PortalHeader />
      <div className="portal-cab-layout">
        <SiteSidebar active={sidebar.active} location={sidebar.location} />
        <main className="portal-cab-main portal-section">
          <SectionSubnav active={sidebar.active} location={sidebar.location} user={user} />
          {children}
        </main>
      </div>
      <PortalFooter />
    </div>
  );
}
