import type { ReactNode } from "react";
import { PortalFooter } from "../portal/PortalFooter";
import { PortalHeader } from "../portal/PortalHeader";
import "../portal/portal.css";
import "../portal/portalSection.css";
import "./adminShell.css";

/**
 * Каркас админки в портальном дизайне: общая шапка портала + янтарная плашка
 * «Админка» (служебная зона должна отличаться от пользовательских страниц с
 * первого взгляда) + контейнер 1440px. AdminSubnav страницы рендерят сами —
 * он уже есть в каждой из них с правильным activePath.
 */
export function AdminShell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="portal-section-page">
      <PortalHeader />
      <div className="admin-shell-banner">
        <div className="admin-shell-banner-inner">
          <span className="admin-shell-badge">Админка</span>
          <span className="admin-shell-title">{title}</span>
        </div>
      </div>
      <main className="portal-section admin-shell-main">{children}</main>
      <PortalFooter />
    </div>
  );
}
