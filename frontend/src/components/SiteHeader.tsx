import type { ReactNode } from "react";
import { SITE_HOME_HREF, SITE_LOGO_SRC, SITE_NAME } from "../lib/siteBrand";
import { ThemeToggle } from "./ThemeToggle";

export type SiteNavItem = {
  href: string;
  label: string;
  /* Пункт-иконка: вместо текста рисуется пиктограмма, label уходит в aria/title. */
  icon?: "home";
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
  if (item.icon) {
    return "site-nav-link site-nav-link-icon";
  }
  return "site-nav-link";
}

function HomeIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 11.2 12 4.4l8 6.8" />
      <path d="M6.3 10.4V19.2h11.4V10.4" />
    </svg>
  );
}

function isNavActive(path: string, href: string): boolean {
  if (href.startsWith("/admin")) {
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
                aria-label={item.icon ? item.label : undefined}
                title={item.icon ? item.label : undefined}
              >
                {item.icon === "home" ? <HomeIcon /> : item.label}
              </a>
            ))}
          </nav>
        )}

        <div className="site-topbar-actions">
          <ThemeToggle />
          {actions}
        </div>
      </div>
    </header>
  );
}
