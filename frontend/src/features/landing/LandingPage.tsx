import { useEffect, useState } from "react";
import { PlatformBadge } from "../../components/PlatformBadge";
import { SiteHeader } from "../../components/SiteHeader";
import { getCurrentUser } from "../../lib/api";
import { PROJECT_MISSION_LANDING } from "../../lib/projectMission";
import { SITE_NAME, SITE_PUBLIC_HOME_HREF } from "../../lib/siteBrand";
import { PUBLIC_NAV_ITEMS } from "../../lib/siteNav";

const FEATURES = [
  {
    title: "Всё в одном месте",
    text: "Пробежки и волонтёрство из 5 вёрст, С95 и parkrun — без переключения между сайтами и таблицами в разных форматах.",
  },
  {
    title: "Карта локаций",
    text: "Где вы уже были, какие площадки ещё не посещали, фильтр по системам — удобно планировать поездки на субботние старты.",
  },
  {
    title: "Аналитика",
    text: "Темп, PR, серии суббот, индекс волонтёрства, топ локаций — наглядная статистика для тех, кто бегает регулярно.",
  },
  {
    title: "Без ручного ввода",
    text: "Привяжите профили один раз — данные подтягиваются из публичных страниц беговых систем и обновляются по запросу.",
  },
] as const;

const AUDIENCE = [
  "участникам субботних парковых пробежек, которые бегают в нескольких системах;",
  "волонтёрам, которые хотят видеть вклад и историю ролей;",
  "тем, кому не хватает единой картины: сколько локаций, какой темп, где ещё не был;",
  "тем, кто откладывает первую пробежку в новой системе — когда статистика уже собрана в одном месте, порог входа ниже.",
] as const;

export function LandingPage() {
  const [checkingAuth, setCheckingAuth] = useState(true);

  useEffect(() => {
    const path = window.location.pathname.replace(/\/$/, "") || "/";
    if (path !== "/") {
      setCheckingAuth(false);
      return;
    }
    getCurrentUser()
      .then(() => {
        window.location.href = "/dashboard";
      })
      .catch(() => {
        setCheckingAuth(false);
      });
  }, []);

  if (checkingAuth) {
    return (
      <main className="landing-page">
        <p className="muted">Загрузка…</p>
      </main>
    );
  }

  return (
    <>
      <SiteHeader
        homeHref={SITE_PUBLIC_HOME_HREF}
        navItems={PUBLIC_NAV_ITEMS}
        activePath="/"
        actions={
          <a className="btn primary btn-sm" href="/login">
            Войти
          </a>
        }
      />
      <main className="landing-page shell-content">
      <section className="landing-hero card">
        <p className="about-eyebrow">Личный кабинет субботнего бегуна</p>
        <h1 className="landing-title">Статистика пробежек и волонтёрства — в одном месте</h1>
        <p className="landing-lead">
          {SITE_NAME} собирает ваши данные из <strong>5 вёрст</strong>, <strong>С95</strong> и{" "}
          <strong>parkrun</strong>: история финишей, роли волонтёра, карта площадок и аналитика.
          Проект неофициальный — для участников, которым важна наглядная сводка по всем системам.
        </p>
        <p className="landing-mission">{PROJECT_MISSION_LANDING}</p>
        <div className="landing-hero-actions">
          <a className="btn primary" href="/login">
            Войти через Telegram
          </a>
          <a className="btn secondary" href="/demo">
            Посмотреть демо
          </a>
        </div>
        <p className="muted landing-hero-note">
          В демо можно пройти по разделам личного кабинета на примере реального профиля — только
          просмотр, без привязки своих аккаунтов.
        </p>
      </section>

      <section className="landing-section">
        <h2 className="landing-section-title">Для кого</h2>
        <ul className="landing-list">
          {AUDIENCE.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>

      <section className="landing-section">
        <h2 className="landing-section-title">Чем полезен</h2>
        <div className="landing-features">
          {FEATURES.map((feature) => (
            <article key={feature.title} className="card landing-feature-card">
              <h3 className="landing-feature-title">{feature.title}</h3>
              <p>{feature.text}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="card landing-demo-cta">
        <h2 className="landing-section-title">Как это выглядит</h2>
        <p>
          В демо открыт профиль автора с привязанными аккаунтами во всех трёх системах — те же
          разделы, что после входа: главная с аналитикой, пробежки, волонтёрство и карта.
        </p>
        <div className="landing-platform-badges">
          <PlatformBadge code="five_verst" />
          <PlatformBadge code="s95" />
          <PlatformBadge code="parkrun" />
        </div>
        <a className="btn primary" href="/demo">
          Открыть демо-профиль
        </a>
      </section>

      <section className="landing-footer-links">
        <a href="/about">О проекте, контакты и политика данных</a>
        <span className="muted">·</span>
        <a href="/login">Вход через Telegram</a>
      </section>
    </main>
    </>
  );
}
