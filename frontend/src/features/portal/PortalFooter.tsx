import { useEffect, useState } from "react";
import { PORTAL_ABOUT_HREF, PORTAL_BLOG_HREF, PORTAL_UPDATES_HREF } from "../../lib/portalRoutes";
import { fetchLatestReleaseVersion } from "./releaseTypes";
import "./portal.css";

/**
 * Единый подвал портала: ссылки второго плана + номер текущей версии сайта
 * (последний опубликованный релиз, кликом ведёт на «Обновления»). Пока релизы
 * не опубликованы, номер просто не показывается.
 */
export function PortalFooter() {
  const [version, setVersion] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchLatestReleaseVersion()
      .then((value) => {
        if (!cancelled) {
          setVersion(value);
        }
      })
      .catch(() => {
        /* подвал живёт и без номера версии */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <footer className="portal-footer">
      <div className="portal-footer-inner">
        <span className="portal-footer-brand">
          run5k<span className="portal-footer-tld">.run</span> — статистика парковых пробежек
        </span>
        <nav className="portal-footer-nav" aria-label="Ссылки в подвале">
          <a className="portal-footer-link" href={PORTAL_UPDATES_HREF}>
            Обновления
          </a>
          <a className="portal-footer-link" href={PORTAL_BLOG_HREF}>
            Блог
          </a>
          <a className="portal-footer-link" href={PORTAL_ABOUT_HREF}>
            О проекте
          </a>
          {version && (
            <a
              className="portal-footer-version"
              href={PORTAL_UPDATES_HREF}
              title="Что нового на сайте"
            >
              v{version}
            </a>
          )}
        </nav>
      </div>
    </footer>
  );
}
