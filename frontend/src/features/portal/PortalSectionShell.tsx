import type { ReactNode } from "react";
import { PortalHeader } from "./PortalHeader";
import { SiteSidebar, type SiteSidebarProps } from "./SiteSidebar";
import { PortalSectionBottomNav } from "./PortalSectionBottomNav";
import "./portal.css";
import "./portalSection.css";

/**
 * Каркас для разделов портала верхнего уровня (Локации, Рейтинги, Бэклог,
 * публичный профиль). Общая шапка `<PortalHeader/>` + контейнер 1440px.
 *
 * С пропом `sidebar` рендерится ЕДИНЫЙ сайдбар сайта (SiteSidebar) в том же
 * лейауте, что и личный кабинет (.portal-cab-layout) — сворачивание и стили
 * общие. Без пропа — просто центрированный контейнер (публичный профиль).
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
      <div className="portal-cab-layout">
        <SiteSidebar active={sidebar.active} location={sidebar.location} />
        <main className="portal-cab-main portal-section">{children}</main>
      </div>
      {/* На телефоне сайдбар скрыт: без этой панели раздел оставался вообще
          без навигации — вернуться в кабинет было не по чему. */}
      <PortalSectionBottomNav active={sidebar.active} />
    </div>
  );
}
