import { SiteHeader } from "../components/SiteHeader";
import { isLegacyGrafanaPath, LEGACY_SITE_HREF, LEGACY_SITE_LABEL, legacyGrafanaHref, SITE_PUBLIC_HOME_HREF } from "../lib/siteBrand";
import { PUBLIC_NAV_ITEMS } from "../lib/siteNav";

export function NotFoundPage() {
  const path = window.location.pathname;
  const legacyTarget = isLegacyGrafanaPath(path)
    ? legacyGrafanaHref(path, window.location.search, window.location.hash)
    : null;

  return (
    <>
      <SiteHeader homeHref={SITE_PUBLIC_HOME_HREF} navItems={PUBLIC_NAV_ITEMS} activePath="" />
      <main className="app">
        <section className="card">
          {legacyTarget ? (
            <>
              <h1 className="section-title">Эта страница переехала</h1>
              <p className="muted">
                Дашборды Grafana теперь на{" "}
                <a href={LEGACY_SITE_HREF}>{LEGACY_SITE_LABEL}</a>. Старые ссылки вида{" "}
                <code>{path}</code> с домена run5k.run больше не работают.
              </p>
              <p>
                <a href={legacyTarget} className="btn primary">
                  Открыть на {LEGACY_SITE_LABEL}
                </a>
              </p>
            </>
          ) : (
            <>
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
            </>
          )}
        </section>
      </main>
    </>
  );
}
