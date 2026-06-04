import { SiteHeader } from "../components/SiteHeader";
import { SITE_PUBLIC_HOME_HREF } from "../lib/siteBrand";
import { PUBLIC_NAV_ITEMS } from "../lib/siteNav";

export function NotFoundPage() {
  const path = window.location.pathname;

  return (
    <>
      <SiteHeader homeHref={SITE_PUBLIC_HOME_HREF} navItems={PUBLIC_NAV_ITEMS} activePath="" />
      <main className="app">
        <section className="card">
          <h1 className="section-title">Страница не найдена</h1>
          <p className="muted">
            Адрес <code>{path}</code> не зарегистрирован в приложении.
          </p>
          <p>
            <a href="/admin" className="btn secondary">
              В админку
            </a>{" "}
            <a href="/dashboard" className="btn primary">
              В личный кабинет
            </a>
          </p>
        </section>
      </main>
    </>
  );
}
