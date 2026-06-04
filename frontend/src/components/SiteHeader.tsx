import type { ReactNode } from "react";
import { SITE_HOME_HREF, SITE_LOGO_SRC, SITE_NAME } from "../lib/siteBrand";

export type SiteNavItem = {
  href: string;
  label: string;
  adminOnly?: boolean;
  adminStyle?: boolean;
  muted?: boolean;
};

type SiteHeaderProps = {
  homeHref?: string;
  navItems?: SiteNavItem[];
  activePath?: string;
  showAdminNav?: boolean;
  actions?: ReactNode;
};

function navClassName(item: SiteNavItem, isActive: boolean): string {
  if (isActive && item.adminStyle) {
    return "site-nav-link site-nav-link-admin active";
  }
  if (isActive) {
    return "site-nav-link active";
  }
  if (item.adminStyle) {
    return "site-nav-link site-nav-link-admin";
  }
  if (item.muted) {
    return "site-nav-link site-nav-link-muted";
  }
  return "site-nav-link";
}

function isNavActive(path: string, href: string): boolean {
  if (href === "/admin") {
    return path === href || path.startsWith("/admin");
  }
  return path === href;
}

export function SiteHeader({
  homeHref = SITE_HOME_HREF,
  navItems = [],
  activePath,
  showAdminNav = false,
  actions,
}: SiteHeaderProps) {
  const path = activePath ?? (window.location.pathname.replace(/\/$/, "") || "/");
  const visibleNav = navItems.filter((item) => !item.adminOnly || showAdminNav);

  return (
    <header className="site-topbar">
      <div className="site-topbar-inner">
        <a href={homeHref} className="site-brand" aria-label={`${SITE_NAME} — на главную`}>
          <img src={SITE_LOGO_SRC} alt="" className="site-brand-logo" width={44} height={44} />
          <span className="site-brand-name">{SITE_NAME}</span>
        </a>

        {visibleNav.length > 0 && (
          <nav className="site-topbar-nav" aria-label="Основная навигация">
            {visibleNav.map((item) => (
              <a
                key={item.href}
                href={item.href}
                className={navClassName(item, isNavActive(path, item.href))}
              >
                {item.label}
              </a>
            ))}
          </nav>
        )}

        {actions ? <div className="site-topbar-actions">{actions}</div> : null}
      </div>
    </header>
  );
}
