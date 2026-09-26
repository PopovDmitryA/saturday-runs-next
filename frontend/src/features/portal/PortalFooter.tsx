import { useEffect, useState, type ReactNode } from "react";
import { PORTAL_ABOUT_PRIVACY_HREF, PORTAL_HOME_HREF, PORTAL_LOGIN_HREF, PORTAL_UPDATES_HREF } from "../../lib/portalRoutes";
import { useOptionalUser } from "../../lib/useOptionalUser";
import { resolveNavState } from "./nav/navState";
import { CABINET_LABEL, CABINET_TABS, type NavSection } from "./nav/siteNav";
import { fetchLatestReleaseVersion } from "./releaseTypes";
import "./portal.css";

/**
 * Год запуска проекта. Нижняя строка показывает «© 2024–текущий», поэтому
 * при смене года ничего править руками не нужно.
 */
const FOUNDED_YEAR = 2024;

const TELEGRAM_CHANNEL_HREF = "https://t.me/popov_way";

type FooterLink = { key: string; href: string; label: string };

type FooterColumn = { title: string; links: FooterLink[] };

/**
 * Колонки подвала — из того же дерева, что рельс, шапка и «Меню»: те же
 * названия разделов и те же адреса (решение Дмитрия 26.09.2026). Раньше
 * подвал был ещё одним меню, написанным руками: «Последние пробежки» вместо
 * «Итогов», «Личный кабинет» в колонке «Участнику», и не было ни единого
 * протокола, ни оргкабинета.
 *
 * По смыслу колонки прежние: статистика сайта, кабинет, проект.
 */
function footerColumns(sections: NavSection[]): FooterColumn[] {
  const byKey = (key: NavSection["key"]) => sections.find((section) => section.key === key);
  const itemsOf = (section: NavSection | undefined) => section?.groups.flatMap((group) => group.items) ?? [];
  const sectionLink = (section: NavSection | undefined): FooterLink[] =>
    section ? [{ key: section.key, href: section.href, label: section.shortLabel }] : [];

  const results = byKey("results");
  const stats: FooterColumn = {
    title: "Статистика",
    links: [
      ...sectionLink(results),
      // «Единый протокол» — вторая страница «Итогов»; первая и есть /results.
      ...itemsOf(results)
        .filter((link) => link.href !== results?.href)
        .map((link) => ({ key: link.key, href: link.href, label: link.label })),
      ...sectionLink(byKey("locations")),
      ...sectionLink(byKey("ratings")),
      ...sectionLink(byKey("organizer")),
    ],
  };

  // Гостю вкладки кабинета ведут на вход — это и есть его путь туда; сам
  // раздел у гостя подписан «Войти», как на рельсе и нижней панели.
  const me = byKey("me");
  const meItems = itemsOf(me);
  const settings = itemsOf(byKey("account")).find((link) => link.key === "settings");
  const cabinet: FooterColumn = {
    title: CABINET_LABEL,
    links: [
      ...sectionLink(me),
      ...(["runs", "achievements"] as const).map((key) => ({
        key,
        href: meItems.find((link) => link.key === key)?.href ?? PORTAL_LOGIN_HREF,
        label: CABINET_TABS.find((tab) => tab.key === key)?.label ?? key,
      })),
      ...(settings ? [{ key: settings.key, href: settings.href, label: settings.label }] : []),
    ],
  };

  const project = byKey("project");
  const projectColumn: FooterColumn = {
    title: project?.label ?? "О проекте",
    links: [
      ...itemsOf(project).map((link) => ({ key: link.key, href: link.href, label: link.label })),
      // Якорь внутри «О проекте» — отдельной страницы в дереве у него нет.
      { key: "privacy", href: PORTAL_ABOUT_PRIVACY_HREF, label: "Данные и приватность" },
    ],
  };

  return [stats, cabinet, projectColumn];
}

function TelegramIcon(): ReactNode {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M21.5 2.7 2.6 10c-.9.35-.87 1.6.05 1.9l4.6 1.44 1.73 5.5c.27.86 1.36 1.06 1.92.36l2.05-2.53 4.5 3.3c.77.57 1.87.15 2.06-.79L22.9 4.1c.2-1-.55-1.72-1.4-1.4Z" />
    </svg>
  );
}

/**
 * Подвал портала: разделы сайта колонками, системы-источники данных, год
 * основания и номер текущей версии сайта (последний опубликованный релиз,
 * кликом ведёт на «Обновления»). Пока релизы не опубликованы, номер просто
 * не показывается.
 *
 * Подвал живёт на всех страницах, поэтому обходится без своих запросов, кроме
 * лёгкого /api/releases/latest, и без гейтов: гостю ссылки кабинета ведут на
 * вход — это и есть его путь туда.
 */
export function PortalFooter() {
  const [version, setVersion] = useState<string | null>(null);
  const optionalUser = useOptionalUser();

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

  const year = Math.max(FOUNDED_YEAR, new Date().getFullYear());
  const columns = footerColumns(resolveNavState({ user: optionalUser }).sections);

  return (
    <footer className="portal-footer">
      <div className="portal-footer-inner">
        <div className="portal-footer-top">
          <div className="portal-footer-about">
            <a className="portal-footer-brand" href={PORTAL_HOME_HREF}>
              run5k<span className="portal-footer-tld">.run</span>
            </a>
            <p className="portal-footer-tagline">Статистика парковых пробежек</p>
            <p className="portal-footer-note">
              Проект живёт с {FOUNDED_YEAR} года: собираем результаты субботних стартов
              5 вёрст, S95, parkrun и RunPark в одну историю участника.
            </p>
            <a
              className="portal-footer-channel"
              href={TELEGRAM_CHANNEL_HREF}
              target="_blank"
              rel="noreferrer"
            >
              <TelegramIcon />
              Канал в Telegram
            </a>
          </div>

          <nav className="portal-footer-columns" aria-label="Разделы сайта">
            {columns.map((column) => (
              <div className="portal-footer-column" key={column.title}>
                <h2 className="portal-footer-column-title">{column.title}</h2>
                <ul className="portal-footer-list">
                  {column.links.map((link) => (
                    <li key={link.key}>
                      <a className="portal-footer-link" href={link.href}>
                        {link.label}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </nav>
        </div>

        <div className="portal-footer-bottom">
          <span className="portal-footer-copy">
            © {FOUNDED_YEAR}—{year} run5k.run
          </span>
          <span className="portal-footer-sources">
            Данные систем 5 вёрст, S95, parkrun и RunPark. Проект независимый и некоммерческий.
          </span>
          {version && (
            <a
              className="portal-footer-version"
              href={PORTAL_UPDATES_HREF}
              title="Что нового на сайте"
            >
              v{version}
            </a>
          )}
        </div>
      </div>
    </footer>
  );
}
