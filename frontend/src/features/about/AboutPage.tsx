import { useEffect, useState, type ReactNode } from "react";
import { AppShell } from "../../components/AppShell";
import { SiteHeader } from "../../components/SiteHeader";
import { PlatformProfileLogo } from "../../components/PlatformProfileLogo";
import { probeCurrentUser } from "../../lib/api";
import { PROJECT_MISSION } from "../../lib/projectMission";
import { LEGACY_SITE_HREF, LEGACY_SITE_LABEL, SITE_NAME, SITE_PUBLIC_HOME_HREF } from "../../lib/siteBrand";
import { PUBLIC_NAV_ITEMS } from "../../lib/siteNav";

const TELEGRAM_CONTACTS = [
  {
    kind: "personal",
    title: "Личный Telegram",
    description: "Написать автору напрямую",
    href: "https://t.me/Popov_Dmitry",
    label: "@Popov_Dmitry",
    icon: "user",
  },
  {
    kind: "channel",
    title: "Канал",
    description: "Блог о субботних пробежках, жизни и цифрах",
    href: "https://t.me/popov_way",
    label: "@popov_way",
    icon: "channel",
  },
  {
    kind: "chat",
    title: "Чат",
    description: "Обсуждение, вопросы и обратная связь по сайту",
    href: "https://t.me/popov_talk",
    label: "@popov_talk",
    icon: "chat",
  },
] as const;

const AUTHOR_PROFILES = [
  {
    code: "five_verst",
    name: "5 вёрст",
    href: "https://5verst.ru/userstats/790103773/",
    subtitle: "Дмитрий ПОПОВ · ID 790103773",
  },
  {
    code: "s95",
    name: "С95",
    href: "https://s95.ru/athletes/5207/",
    subtitle: "Дмитрий ПОПОВ · athlete 5207",
  },
  {
    code: "parkrun",
    name: "parkrun",
    href: "https://www.parkrun.org.uk/parkrunner/7035519/",
    subtitle: "Дмитрий ПОПОВ · A7035519",
  },
] as const;

function TelegramIcon({ kind }: { kind: (typeof TELEGRAM_CONTACTS)[number]["icon"] }) {
  if (kind === "user") {
    return (
      <svg className="about-link-icon-svg" viewBox="0 0 24 24" aria-hidden>
        <path
          fill="currentColor"
          d="M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4Zm0 2c-3.31 0-6 1.57-6 3.5V20h12v-2.5C18 15.57 15.31 14 12 14Z"
        />
      </svg>
    );
  }
  if (kind === "channel") {
    return (
      <svg className="about-link-icon-svg" viewBox="0 0 24 24" aria-hidden>
        <path
          fill="currentColor"
          d="M18 11c0 2.76-2.24 5-5 5h-1.17L10 21.17V16H5c-2.76 0-5-2.24-5-5s2.24-5 5-5h5V3.83L12.83 2H13c2.76 0 5 2.24 5 5v4zm-2 0V7H7v8h9z"
        />
      </svg>
    );
  }
  return (
    <svg className="about-link-icon-svg" viewBox="0 0 24 24" aria-hidden>
      <path
        fill="currentColor"
        d="M20 2H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h2v2l3.24-3.24C7.58 14.32 8.35 14 9 14c3.87 0 7-3.13 7-7s-3.13-7-7-7-7 3.13-7 7c0 1.04.23 2.03.64 2.92L4 18.83V16H4V4h16v10zm-8 1c2.21 0 4 1.79 4 4s-1.79 4-4 4-4-1.79-4-4 1.79-4 4-4z"
      />
    </svg>
  );
}

function HistorySiteIcon() {
  return (
    <svg className="about-link-icon-svg" viewBox="0 0 24 24" aria-hidden>
      <path
        fill="currentColor"
        d="M13 3a9 9 0 1 0 8.95 10h-2.04A7 7 0 1 1 13 5V3Zm-1 4h2v5.17l3.59 3.59-1.41 1.41L12 12.58V7Z"
      />
    </svg>
  );
}

function ExternalLinkIcon() {
  return (
    <svg className="about-link-card-arrow" viewBox="0 0 24 24" aria-hidden>
      <path
        fill="currentColor"
        d="M14 3h7v7h-2V6.41l-9.29 9.3-1.42-1.42 9.3-9.29H14V3ZM5 5h6V3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-6h-2v6H5V5Z"
      />
    </svg>
  );
}

export function AboutPageContent() {
  return (
    <div className="about-page">
      <section className="about-section about-section-legacy-top">
        <a
          className="about-link-card about-link-card-legacy"
          href={LEGACY_SITE_HREF}
          target="_blank"
          rel="noreferrer"
        >
          <span className="about-link-icon about-link-icon-legacy">
            <HistorySiteIcon />
          </span>
          <span className="about-link-card-body">
            <span className="about-link-card-title">Прежняя версия сайта</span>
            <span className="about-link-card-label about-link-card-label-legacy">{LEGACY_SITE_LABEL}</span>
            <span className="about-link-card-desc">
              Grafana, рейтинги и дашборды — прежняя версия на grafana.run5k.run.
            </span>
          </span>
          <ExternalLinkIcon />
        </a>
      </section>

      <section className="about-hero card">
        <p className="about-eyebrow">{SITE_NAME}</p>
        <h2 className="about-hero-title">Единая статистика субботних пробежек</h2>
        <p className="about-lead">
          Личный кабинет для участников <strong>5 вёрст</strong>, <strong>С95</strong> и{" "}
          <strong>parkrun</strong>: пробежки, волонтёрство, карта локаций и аналитика в одном
          месте — без переключения между сайтами разных систем.
        </p>
        <blockquote className="about-mission about-mission-quote">
          <p>{PROJECT_MISSION}</p>
          <footer>— Дмитрий ПОПОВ, автор проекта</footer>
        </blockquote>
      </section>

      <section className="about-disclaimer card" aria-label="Дисклеймер">
        <div className="about-disclaimer-icon" aria-hidden>
          !
        </div>
        <div>
          <h3 className="about-section-title">Неофициальный проект</h3>
          <p>
            Этот сайт <strong>не является официальным ресурсом</strong> 5 вёрст, С95, parkrun или
            любой другой беговой системы. Мы не аффилированы с организаторами стартов и не
            представляем их интересы.
          </p>
          <p>
            Все данные на сайте получены из <strong>открытых публичных источников</strong>{" "}
            (официальные сайты беговых систем) и <strong>не принадлежат автору проекта</strong>.
            В личном кабинете отображается ваша статистика после входа (Telegram, VK или Яндекс) и
            привязки профилей; часть сводных данных может обновляться и без отдельного запроса
            пользователя.
          </p>
        </div>
      </section>

      <section id="privacy" className="card about-privacy">
        <h3 className="about-section-title">Согласие на обработку персональных данных</h3>
        <div className="about-privacy-body">
          <p>
            Используя {SITE_NAME}, вы подтверждаете согласие на обработку персональных данных.
            Фиксация согласия происходит при входе через Telegram-бота (кнопка «Принимаю»).
          </p>
          <h4 className="about-privacy-subtitle">Что это за сервис</h4>
          <p>
            {SITE_NAME} — неофициальный личный проект. Сервис не связан с организаторами 5 вёрст,
            С95, parkrun и других беговых систем. Статистика из разных платформ собирается в одном
            месте, чтобы участникам было проще решиться начать вести учёт в новой беговой системе.
          </p>
          <h4 className="about-privacy-subtitle">Какие данные обрабатываются</h4>
          <ul>
            <li>
              данные учётной записи при входе (Telegram: идентификатор, имя пользователя и отображаемое
              имя; VK и Яндекс: идентификатор и имя профиля, при наличии — e-mail);
            </li>
            <li>
              данные публичных профилей в беговых системах, которые вы добровольно привязываете
              (имя, статистика пробежек и волонтёрства, локации, результаты и иные сведения со
              страниц профилей на сайтах платформ);
            </li>
            <li>технические данные сессии для авторизации.</li>
          </ul>
          <h4 className="about-privacy-subtitle">Цель обработки</h4>
          <p>
            Формирование и отображение личного кабинета: сводная статистика, история активности,
            карта локаций и аналитика в одном месте.
          </p>
          <h4 className="about-privacy-subtitle">Основание и отзыв</h4>
          <p>
            Согласие даётся добровольным действием: вход через выбранный способ и привязка профилей на
            сайте.
            Вы можете прекратить использование сервиса, отвязать профили платформ и написать автору
            через контакты ниже.
          </p>
        </div>
      </section>

      <section className="card about-author">
        <div className="about-author-badge" aria-hidden>
          DP
        </div>
        <div className="about-author-body">
          <h3 className="about-section-title">Об авторе</h3>
          <p className="about-author-name">Дмитрий ПОПОВ</p>
          <p>
            Я такой же участник парковых пробежек, как и вы — просто очень хотел видеть всю
            статистику по всем системам в одном месте. {SITE_NAME} — личный проект: я собираю для
            себя и для таких же участников удобный обзор пробежек, волонтёрств и локаций, не
            претендуя на роль официального сервиса.
          </p>
        </div>
      </section>

      <section className="about-section">
        <h3 className="about-section-title">Контакты</h3>
        <div className="about-link-grid">
          {TELEGRAM_CONTACTS.map((item) => (
            <a
              key={item.href}
              className={`about-link-card about-link-card-telegram about-link-card-${item.kind}`}
              href={item.href}
              target="_blank"
              rel="noreferrer"
            >
              <span className="about-link-icon about-link-icon-telegram">
                <TelegramIcon kind={item.icon} />
              </span>
              <span className="about-link-card-body">
                <span className="about-link-card-title">{item.title}</span>
                <span className="about-link-card-label">{item.label}</span>
                <span className="about-link-card-desc">{item.description}</span>
              </span>
              <ExternalLinkIcon />
            </a>
          ))}
        </div>
      </section>

      <section className="about-section">
        <h3 className="about-section-title">Профили автора в беговых системах</h3>
        <div className="about-link-grid about-link-grid-profiles">
          {AUTHOR_PROFILES.map((profile) => (
            <a
              key={profile.href}
              className={`about-link-card about-link-card-profile about-link-card-profile-${profile.code}`}
              href={profile.href}
              target="_blank"
              rel="noreferrer"
            >
              <span className={`about-link-icon about-link-icon-platform about-link-icon-platform-${profile.code}`}>
                <PlatformProfileLogo code={profile.code} />
              </span>
              <span className="about-link-card-body">
                <span className="about-link-card-title">{profile.name}</span>
                <span className="about-link-card-desc">{profile.subtitle}</span>
              </span>
              <ExternalLinkIcon />
            </a>
          ))}
        </div>
      </section>
    </div>
  );
}

function AboutPublicShell({ children }: { children: ReactNode }) {
  return (
    <div className="about-public-shell">
      <SiteHeader
        homeHref={SITE_PUBLIC_HOME_HREF}
        navItems={PUBLIC_NAV_ITEMS}
        activePath="/about"
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

export function AboutPage() {
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

  const content = <AboutPageContent />;

  if (authed) {
    return (
      <AppShell title="О проекте" activePath="/about">
        {content}
      </AppShell>
    );
  }

  return <AboutPublicShell>{content}</AboutPublicShell>;
}
