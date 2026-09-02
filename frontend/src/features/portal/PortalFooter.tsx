import { useEffect, useState, type ReactNode } from "react";
import {
  cabinetTabHref,
  PORTAL_ABOUT_HREF,
  PORTAL_ABOUT_PRIVACY_HREF,
  PORTAL_BLOG_HREF,
  PORTAL_CABINET_SETTINGS_HREF,
  PORTAL_HOME_HREF,
  PORTAL_LOGIN_HREF,
  PORTAL_UPDATES_HREF,
} from "../../lib/portalRoutes";
import { useOptionalUser } from "../../lib/useOptionalUser";
import { fetchLatestReleaseVersion } from "./releaseTypes";
import "./portal.css";

/**
 * Год запуска проекта. Нижняя строка показывает «© 2024–текущий», поэтому
 * при смене года ничего править руками не нужно.
 */
const FOUNDED_YEAR = 2024;

const TELEGRAM_CHANNEL_HREF = "https://t.me/popov_way";

type FooterLink = {
  href: string;
  label: string;
  /** Ссылка ведёт в личный кабинет: гостю подменяется на вход. */
  cabinetTab?: "dashboard" | "runs" | "achievements" | "map";
  /** Страница под авторизацией без вкладки кабинета (Настройки) — гостю тоже вход. */
  authOnly?: boolean;
  external?: boolean;
};

type FooterColumn = { title: string; links: FooterLink[] };

const FOOTER_COLUMNS: FooterColumn[] = [
  {
    title: "Статистика",
    links: [
      { href: PORTAL_HOME_HREF, label: "Главная" },
      { href: "/locations", label: "Локации" },
      { href: "/results", label: "Последние пробежки" },
      { href: "/ratings", label: "Рейтинги" },
    ],
  },
  {
    title: "Участнику",
    links: [
      { href: PORTAL_LOGIN_HREF, label: "Личный кабинет", cabinetTab: "dashboard" },
      { href: PORTAL_LOGIN_HREF, label: "Мои пробежки", cabinetTab: "runs" },
      { href: PORTAL_LOGIN_HREF, label: "Достижения", cabinetTab: "achievements" },
      { href: PORTAL_CABINET_SETTINGS_HREF, label: "Настройки", authOnly: true },
    ],
  },
  {
    title: "Проект",
    links: [
      { href: PORTAL_ABOUT_HREF, label: "О проекте" },
      { href: PORTAL_UPDATES_HREF, label: "Обновления" },
      { href: PORTAL_BLOG_HREF, label: "Блог" },
      { href: "/backlog", label: "Бэклог идей" },
      { href: PORTAL_ABOUT_PRIVACY_HREF, label: "Данные и приватность" },
    ],
  },
];

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
  const user = optionalUser ?? null;

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
  const linkHref = (link: FooterLink) => {
    if (link.cabinetTab) {
      return user ? cabinetTabHref(user, link.cabinetTab) : PORTAL_LOGIN_HREF;
    }
    return link.authOnly && !user ? PORTAL_LOGIN_HREF : link.href;
  };

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
            {FOOTER_COLUMNS.map((column) => (
              <div className="portal-footer-column" key={column.title}>
                <h2 className="portal-footer-column-title">{column.title}</h2>
                <ul className="portal-footer-list">
                  {column.links.map((link) => (
                    <li key={link.label}>
                      <a
                        className="portal-footer-link"
                        href={linkHref(link)}
                        {...(link.external ? { target: "_blank", rel: "noreferrer" } : {})}
                      >
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
