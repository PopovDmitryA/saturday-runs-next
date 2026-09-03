import { SiteHeader } from "../components/SiteHeader";
import { PortalFooter } from "./portal/PortalFooter";
import { isLegacyGrafanaPath, LEGACY_SITE_LABEL, SITE_PUBLIC_HOME_HREF } from "../lib/siteBrand";
import { PUBLIC_NAV_ITEMS } from "../lib/siteNav";

export function NotFoundPage() {
  const path = window.location.pathname;
  // Адрес из эпохи Grafana на этом домене: объясняем, что старый сайт закрыт,
  // а не отправляем человека обратно на заглушку (см. legacyGrafanaTarget).
  const isLegacy = isLegacyGrafanaPath(path);

  return (
    <>
      <SiteHeader homeHref={SITE_PUBLIC_HOME_HREF} navItems={PUBLIC_NAV_ITEMS} activePath="" />
      <main className="app">
        <section className="card">
          {isLegacy ? (
            <>
              <h1 className="section-title">Старая статистика закрыта</h1>
              <p className="muted">
                <code>{path}</code> — адрес дашборда с прежнего сайта
                ({LEGACY_SITE_LABEL}). Он закрыт, всё переехало сюда, но прямого
                аналога именно у этого дашборда пока нет.
              </p>
              <p>
                <a href="/ratings" className="btn primary">
                  Рейтинги
                </a>{" "}
                <a href="/locations" className="btn secondary">
                  Локации
                </a>{" "}
                <a href={SITE_PUBLIC_HOME_HREF} className="btn secondary">
                  На главную
                </a>
              </p>
              <p className="muted">
                Не хватает данных, которые были на старом дашборде? Напишите
                автору — <a href="https://t.me/Popov_Dmitry">@Popov_Dmitry</a>.
              </p>
            </>
          ) : (
            <>
              <h1 className="section-title">Страница не найдена</h1>
              <p className="muted">
                Адрес <code>{path}</code> не зарегистрирован в приложении.
              </p>
              <p>
                <a href={SITE_PUBLIC_HOME_HREF} className="btn secondary">
                  На главную
                </a>{" "}
                <a href="/dashboard" className="btn primary">
                  В личный кабинет
                </a>
              </p>
            </>
          )}
        </section>
      </main>
      <PortalFooter />
    </>
  );
}
