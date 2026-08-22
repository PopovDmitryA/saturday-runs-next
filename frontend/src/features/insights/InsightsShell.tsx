import { useEffect, useState, type ReactNode } from "react";
import { AppShell } from "../../components/AppShell";
import { SiteHeader } from "../../components/SiteHeader";
import { probeCurrentUser } from "../../lib/api";
import { SITE_PUBLIC_HOME_HREF } from "../../lib/siteBrand";
import { PUBLIC_NAV_ITEMS } from "../../lib/siteNav";

type InsightsShellProps = {
  activePath: string;
  title?: string;
  children: ReactNode;
};

function InsightsPublicShell({ activePath, children }: InsightsShellProps) {
  return (
    <div className="about-public-shell">
      <SiteHeader
        homeHref={SITE_PUBLIC_HOME_HREF}
        navItems={PUBLIC_NAV_ITEMS}
        activePath={activePath}
        actions={
          <div className="demo-shell-actions">
            <a className="btn btn-ghost btn-sm" href={SITE_PUBLIC_HOME_HREF}>
              На главную
            </a>
            <a className="btn btn-ghost btn-sm" href="/demo">
              Демо
            </a>
            <a className="btn primary btn-sm" href="/login">
              Войти
            </a>
          </div>
        }
      />
      <main className="shell-content about-public-main">{children}</main>
    </div>
  );
}

export function InsightsShell({ activePath, title, children }: InsightsShellProps) {
  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => {
    // "unknown" (429/5xx/таймаут) не трогаем: навигация залогиненного
    // не должна схлопываться в гостевую из-за транзиентной ошибки.
    void probeCurrentUser().then((probe) => {
      if (probe.state !== "unknown") {
        setAuthed(probe.state === "authenticated");
      }
    });
  }, []);

  if (authed === null) {
    return (
      <main className="app">
        <p className="muted">Загрузка…</p>
      </main>
    );
  }

  if (authed) {
    return (
      <AppShell title={title ?? "Справочник дашбордов"} activePath={activePath}>
        {children}
      </AppShell>
    );
  }

  return <InsightsPublicShell activePath={activePath}>{children}</InsightsPublicShell>;
}
