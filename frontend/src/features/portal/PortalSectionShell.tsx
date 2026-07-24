import type { ReactNode } from "react";
import { PortalHeader } from "./PortalHeader";
import "./portal.css";
import "./portalSection.css";

/**
 * Каркас для разделов портала верхнего уровня (Локации, Рейтинги) — тех, что
 * доступны из шапки, но не являются вкладками личного кабинета (у тех свой
 * PortalCabinetShell с сайдбаром). Здесь только общая шапка `<PortalHeader/>`
 * + центрированный контейнер шириной 1440px (как у главной портала).
 *
 * Легаси-контент страниц (.card, .data-table, .lb-*, .loc-*) живёт внутри
 * `.portal-section` и рескинится scoped-правилами в portalSection.css — сами
 * компоненты страниц менять не нужно, только заменить их старый шелл на этот.
 */
export function PortalSectionShell({ children }: { children: ReactNode }) {
  return (
    <div className="portal-section-page">
      <PortalHeader />
      <main className="portal-section">{children}</main>
    </div>
  );
}
