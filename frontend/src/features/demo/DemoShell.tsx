import type { ReactNode } from "react";
import { PlatformBadge } from "../../components/PlatformBadge";
import type { AdminPlatformLinkBrief } from "../../lib/api";
import { SITE_PUBLIC_HOME_HREF } from "../../lib/siteBrand";
import { DEMO_NAV_ITEMS } from "../../lib/siteNav";
import { SiteHeader } from "../../components/SiteHeader";

type DemoShellProps = {
  title: string;
  children: ReactNode;
  ownerLabel?: string;
};

export function DemoShell({ title, children, ownerLabel }: DemoShellProps) {
  const path = window.location.pathname.replace(/\/$/, "") || "/demo";

  return (
    <div className="shell demo-shell">
      <SiteHeader
        homeHref="/demo"
        navItems={DEMO_NAV_ITEMS}
        activePath={path}
        actions={
          <div className="demo-shell-actions">
            <a className="btn btn-ghost btn-sm" href={SITE_PUBLIC_HOME_HREF}>
              На главную
            </a>
            <a className="btn primary btn-sm" href="/login">
              Войти
            </a>
          </div>
        }
      />

      <div className="shell-content">
        <div className="banner demo-banner" role="status">
          <strong>Демо-режим</strong>
          {ownerLabel ? (
            <>
              {" "}
              — пример личного кабинета ({ownerLabel}). Только просмотр: привязка профилей и
              настройки недоступны.
            </>
          ) : (
            <> — только просмотр, без возможности изменять данные.</>
          )}
        </div>

        {title !== "Главная" && <h2 className="page-heading visually-hidden">{title}</h2>}

        <main className="shell-main">{children}</main>
      </div>
    </div>
  );
}

type DemoLinkedProfilesProps = {
  links: AdminPlatformLinkBrief[];
};

export function DemoLinkedProfiles({ links }: DemoLinkedProfilesProps) {
  if (links.length === 0) {
    return (
      <section className="card demo-linked-profiles">
        <h2 className="section-title">Привязанные профили</h2>
        <p className="muted">В демо-профиле пока нет привязок к беговым системам.</p>
      </section>
    );
  }

  return (
    <section className="card demo-linked-profiles">
      <h2 className="section-title">Привязанные профили</h2>
      <p className="muted demo-linked-profiles-hint">
        Так выглядят связанные аккаунты после входа — в демо их нельзя изменить.
      </p>
      <ul className="demo-linked-profiles-list">
        {links.map((link) => (
          <li key={`${link.platform_code}-${link.external_user_id}`} className="demo-linked-profile-row">
            <PlatformBadge code={link.platform_code} />
            <div className="demo-linked-profile-body">
              <a href={link.external_url} target="_blank" rel="noreferrer" className="demo-linked-profile-id">
                {link.external_user_id}
              </a>
              {link.display_name && <span className="muted">{link.display_name}</span>}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
