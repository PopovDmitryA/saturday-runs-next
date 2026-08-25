import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import { logout, probeCurrentUser, type User } from "../lib/api";
import { SITE_HOME_HREF } from "../lib/siteBrand";
import { APP_NAV_ITEMS } from "../lib/siteNav";
import { userLabel } from "../lib/userLabel";
import { SiteHeader } from "./SiteHeader";

type AppShellProps = {
  title: string;
  children: ReactNode;
  activePath?: string;
};

export function AppShell({ title, children, activePath }: AppShellProps) {
  const path = activePath ?? (window.location.pathname.replace(/\/$/, "") || "/");
  const [user, setUser] = useState<User | null>(null);

  const loadUser = useCallback(async () => {
    const probe = await probeCurrentUser();
    if (probe.state === "authenticated") {
      setUser(probe.user);
      return;
    }
    if (probe.state === "guest") {
      setUser(null);
    }
    // state === "unknown": проверить сессию не удалось (429/5xx/таймаут).
    // Прежнее состояние не трогаем — иначе живая сессия выглядит разлогиненной.
  }, []);

  useEffect(() => {
    void loadUser();
  }, [loadUser]);

  const handleLogout = async () => {
    await logout();
    window.location.href = "/login";
  };

  // Дашборд — «домашняя» страница кабинета, отдельный скрытый заголовок ей не нужен.
  const showPageHeading = title !== "Личный кабинет";

  return (
    <div className="shell">
      <SiteHeader
        homeHref={SITE_HOME_HREF}
        navItems={APP_NAV_ITEMS}
        activePath={path}
        showAdminNav={user?.is_admin ?? false}
        actions={
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => void handleLogout()}>
            Выйти
          </button>
        }
      />

      <div className="shell-content">
        {user && (
          <div className="shell-user">
            <div className="shell-user-row">
              <h1 className="shell-user-name">{userLabel(user)}</h1>
            </div>
          </div>
        )}

        {showPageHeading && (
          <h2 className="page-heading visually-hidden">{title}</h2>
        )}

        <main className="shell-main">{children}</main>
      </div>
    </div>
  );
}
