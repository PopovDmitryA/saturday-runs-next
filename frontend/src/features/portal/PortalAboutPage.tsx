import { DonateBlock } from "../../components/DonateBlock";
import { PlatformBadge } from "../../components/PlatformBadge";
import { PROJECT_MISSION } from "../../lib/projectMission";
import { cabinetTabHref, PORTAL_HOME_HREF, PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { useOptionalUser } from "../../lib/useOptionalUser";
import { LEGACY_SITE_HREF, LEGACY_SITE_LABEL } from "../../lib/siteBrand";
import { PortalFooter } from "./PortalFooter";
import { PortalHeader } from "./PortalHeader";
import "./portal.css";

const STEPS = [
  {
    title: "Войдите на сайт",
    text: "Через VK или Яндекс — без паролей и анкет.",
  },
  {
    title: "Привяжите профили",
    text: "Укажите свои страницы в 5 вёрст, S95, parkrun и RunPark — один раз.",
  },
  {
    title: "Получите всю картину",
    text: "Статистика подтягивается из публичных протоколов и обновляется сама.",
  },
] as const;

const FEATURES = [
  {
    icon: (
      <svg viewBox="0 0 16 16">
        <polyline points="1.5,11.5 5.5,7 8.5,10 14.5,4" />
      </svg>
    ),
    title: "Все пробежки",
    text: "Все финиши из всех систем, личные и глобальные рекорды. Гистограмма результатов",
  },
  {
    icon: (
      <svg viewBox="0 0 16 16">
        <path d="M8 13.5S2.5 10 2.5 6.2C2.5 4.2 4.1 3 5.7 3 6.9 3 7.7 3.7 8 4.4 8.3 3.7 9.1 3 10.3 3 11.9 3 13.5 4.2 13.5 6.2 13.5 10 8 13.5 8 13.5Z" />
      </svg>
    ),
    title: "Волонтёрство",
    text: "История ролей и вклад в организацию стартов.",
  },
  {
    icon: (
      <svg viewBox="0 0 16 16">
        <rect x="2" y="3" width="12" height="11" rx="1.5" />
        <line x1="2" y1="6.5" x2="14" y2="6.5" />
        <line x1="5.5" y1="1.5" x2="5.5" y2="4" />
        <line x1="10.5" y1="1.5" x2="10.5" y2="4" />
      </svg>
    ),
    title: "Календарь суббот",
    text: "Тепловая карта всей истории: активные субботы, пропуски и самая длинная серия подряд.",
  },
  {
    icon: (
      <svg viewBox="0 0 16 16">
        <path d="M8 14.5C8 14.5 12.7 9.9 12.7 6.4 12.7 3.8 10.6 1.7 8 1.7 5.4 1.7 3.3 3.8 3.3 6.4 3.3 9.9 8 14.5 8 14.5Z" />
        <circle cx="8" cy="6.4" r="1.7" />
      </svg>
    ),
    title: "Карта визитов",
    text: "Где вы уже бегали и какие площадки ещё впереди — по всем системам сразу.",
  },
  {
    icon: (
      <svg viewBox="0 0 16 16">
        <circle cx="5.5" cy="5.5" r="2.5" />
        <circle cx="11" cy="6.5" r="2" />
        <path d="M2 13.5c0-2.2 1.5-3.5 3.5-3.5s3.5 1.3 3.5 3.5" />
        <path d="M10.5 13.5c.2-1.7 1.2-2.7 2.5-2.7" />
      </svg>
    ),
    title: "Встречи",
    text: "С кем вы чаще всего оказывались в одном протоколе — и личный счёт с каждым.",
  },
  {
    icon: (
      <svg viewBox="0 0 16 16">
        <circle cx="8" cy="8" r="6" />
        <polyline points="8,4.5 8,8 10.5,9.5" />
      </svg>
    ),
    title: "Моя история",
    text: "Лента ваших вех: первый старт, клубы, рекорды, новые регионы — с шер-картинками.",
  },
] as const;

const PRINCIPLES = [
  {
    title: "Неофициальный проект",
    text: "Сайт не связан ни с одной из систем парковых пробежек и не представляет их интересы.",
  },
  {
    title: "Только открытые данные",
    text: "Вся статистика — из публичных протоколов на официальных сайтах беговых систем. Данные не принадлежат автору.",
  },
  {
    title: "Приватность под контролем",
    text: "Публичный профиль можно скрыть в настройках, профили платформ — отвязать, а использование — прекратить в любой момент.",
  },
] as const;

const TELEGRAM_CONTACTS = [
  {
    title: "Личный Telegram",
    description: "Написать автору напрямую",
    href: "https://t.me/Popov_Dmitry",
    label: "@Popov_Dmitry",
  },
  {
    title: "Канал",
    description: "Блог о субботних пробежках, жизни и цифрах",
    href: "https://t.me/popov_way",
    label: "@popov_way",
  },
  {
    title: "Чат",
    description: "Вопросы и обратная связь по сайту",
    href: "https://t.me/popov_talk",
    label: "@popov_talk",
  },
] as const;

const AUTHOR_PROFILES = [
  {
    code: "five_verst",
    href: "https://5verst.ru/userstats/790103773/",
    subtitle: "ID 790103773",
  },
  {
    code: "s95",
    href: "https://s95.ru/athletes/5207/",
    subtitle: "athlete 5207",
  },
  {
    code: "parkrun",
    href: "https://www.parkrun.org.uk/parkrunner/7035519/",
    subtitle: "A7035519",
  },
  {
    code: "runpark",
    href: "https://runpark.ru/Account/Karmas/585CCBA2-4431-4802-8ABD-9C0A483FD4A0",
    subtitle: "ID 585CCBA2",
  },
] as const;

export function PortalAboutPage() {
  // Залогиненному не предлагаем «создать кабинет» — он у него уже есть.
  const user = useOptionalUser();
  const authed = user != null;
  return (
    <>
      <PortalHeader />
      <main className="portal-home portal-about">
        <section className="portal-hero">
          <p className="portal-eyebrow">О проекте</p>
          <h1>Портал статистики парковых пробежек</h1>
          <p className="portal-hero-lead">
            run5k.run объединяет открытые протоколы 5 вёрст, S95, parkrun и RunPark в одну
            картину: общая статистика движения — для всех, личный кабинет — для каждого участника.
          </p>
        </section>

        <section className="portal-panel portal-about-mission" aria-label="Миссия">
          <blockquote>
            <p>{PROJECT_MISSION}</p>
            <footer>— Дмитрий Попов, автор проекта</footer>
          </blockquote>
        </section>

        <section className="portal-panel" aria-label="Как это работает">
          <div className="portal-panel-head">
            <div>
              <h2>Как это работает</h2>
              <p className="portal-panel-sub">Три шага от первого входа до полной статистики</p>
            </div>
          </div>
          <div className="portal-about-steps">
            {STEPS.map((step, index) => (
              <div className="portal-about-step" key={step.title}>
                <span className="portal-about-step-num num">{index + 1}</span>
                <b>{step.title}</b>
                <p>{step.text}</p>
              </div>
            ))}
          </div>
          <div className="portal-about-systems">
            <span className="portal-about-systems-label">Источники данных:</span>
            <PlatformBadge code="five_verst" />
            <PlatformBadge code="s95" />
            <PlatformBadge code="parkrun" />
            <PlatformBadge code="runpark" />
          </div>
        </section>

        <section className="portal-panel" aria-label="Возможности кабинета">
          <div className="portal-panel-head">
            <div>
              <h2>Что внутри кабинета</h2>
              <p className="portal-panel-sub">
                Личная статистика по всем системам сразу
              </p>
            </div>
          </div>
          <div className="portal-about-features">
            {FEATURES.map((feature) => (
              <div className="portal-about-feature" key={feature.title}>
                <span className="portal-about-feature-icon">{feature.icon}</span>
                <b>{feature.title}</b>
                <p>{feature.text}</p>
              </div>
            ))}
          </div>
        </section>

        <section id="privacy" className="portal-panel" aria-label="Честность и данные">
          <div className="portal-panel-head">
            <div>
              <h2>Честность и данные</h2>
              <p className="portal-panel-sub">Три принципа, на которых держится проект</p>
            </div>
          </div>
          <div className="portal-about-principles">
            {PRINCIPLES.map((principle) => (
              <div className="portal-about-principle" key={principle.title}>
                <b>{principle.title}</b>
                <p>{principle.text}</p>
              </div>
            ))}
          </div>
          <details className="portal-about-privacy">
            <summary>Полный текст согласия на обработку персональных данных</summary>
            <div className="portal-about-privacy-body">
              <p>
                Используя run5k.run, вы подтверждаете согласие на обработку персональных данных.
                Согласие фиксируется при первом входе на сайт выбранным способом.
              </p>
              <h4>Что это за сервис</h4>
              <p>
                run5k.run — неофициальный личный проект. Сервис не связан ни с одной из систем
                парковых пробежек и не представляет их интересы. Статистика из разных платформ
                собирается в одном месте, чтобы каждый участник видел свою историю и достижения
                целиком, а решиться начать вести учёт в новой беговой системе было проще.
              </p>
              <h4>Какие данные обрабатываются</h4>
              <ul>
                <li>
                  данные учётной записи при входе (идентификатор и имя профиля, при наличии —
                  e-mail);
                </li>
                <li>
                  данные публичных профилей в беговых системах, которые вы добровольно привязываете
                  (имя, статистика пробежек и волонтёрства, локации, результаты и иные сведения со
                  страниц профилей на сайтах платформ);
                </li>
                <li>технические данные сессии для авторизации.</li>
              </ul>
              <h4>Цель обработки</h4>
              <p>
                Формирование и отображение личного кабинета: сводная статистика, история активности,
                карта локаций и аналитика в одном месте.
              </p>
              <h4>Основание и отзыв</h4>
              <p>
                Согласие даётся добровольным действием: вход через выбранный способ и привязка
                профилей на сайте. Вы можете прекратить использование сервиса, отвязать профили
                платформ и написать автору через контакты ниже.
              </p>
            </div>
          </details>
        </section>

        <section className="portal-panel" aria-label="Бэклог идей">
          {/* Два столбца: слева заголовок с описанием, справа действие. */}
          <div className="portal-panel-head portal-panel-head-split portal-about-backlog-head">
            <div>
              <h2>Бэклог идей</h2>
              <p className="portal-panel-sub">
                Вы можете напрямую влиять на то, каким станет сайт: предлагайте новые разделы и
                статистики, сообщайте о том, что работает не так, голосуйте за чужие идеи. Чем
                больше голосов у карточки, тем раньше она попадёт в работу.
              </p>
            </div>
            <a className="btn secondary portal-about-backlog-cta" href="/backlog">
              Открыть бэклог →
            </a>
          </div>
        </section>

        <section className="portal-panel" aria-label="Автор и контакты">
          <div className="portal-panel-head">
            <div>
              <h2>Автор и контакты</h2>
              <p className="portal-panel-sub">
                Такой же участник субботних пробежек — просто очень любит цифры
              </p>
            </div>
          </div>
          <div className="portal-about-author">
            <div className="portal-about-author-card">
              <span className="portal-about-author-badge" aria-hidden="true">
                DP
              </span>
              <div>
                <b>Дмитрий Попов</b>
                <p>
                  Я собираю для себя и таких же участников удобный обзор пробежек, волонтёрств и
                  локаций по всем системам — не претендуя на роль официального сервиса.
                </p>
              </div>
            </div>
            <div className="portal-about-links">
              <div className="portal-about-links-col">
                <p className="portal-about-links-label">Telegram</p>
                {TELEGRAM_CONTACTS.map((contact) => (
                  <a
                    key={contact.href}
                    className="portal-about-link"
                    href={contact.href}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <b>{contact.label}</b>
                    <span>{contact.description}</span>
                  </a>
                ))}
              </div>
              <div className="portal-about-links-col">
                <p className="portal-about-links-label">Профили автора в системах</p>
                {AUTHOR_PROFILES.map((profile) => (
                  <a
                    key={profile.href}
                    className="portal-about-link"
                    href={profile.href}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <span className="portal-about-link-badge">
                      <PlatformBadge code={profile.code} />
                    </span>
                    <span>{profile.subtitle}</span>
                  </a>
                ))}
              </div>
            </div>
          </div>
          <p className="portal-about-legacy">
            Ищете старые дашборды? Прежняя версия сайта живёт на{" "}
            <a href={LEGACY_SITE_HREF} target="_blank" rel="noreferrer">
              {LEGACY_SITE_LABEL}
            </a>{" "}
            — по мере переноса разделов она уходит на покой.
          </p>
        </section>

        <section className="portal-panel donate-block-panel" aria-label="Поддержать проект">
          <DonateBlock />
        </section>

        <section className="portal-cta">
          <div className="portal-cta-copy">
            <h2>Посмотрите сами</h2>
            <p>
              {authed
                ? "Общая статистика движения — на главной, а ваша личная — в кабинете."
                : "Общая статистика движения открыта без регистрации, а личный кабинет ждёт, когда вы привяжете свои профили."}
            </p>
            <div className="portal-cta-actions">
              <a className="btn primary" href={PORTAL_HOME_HREF}>
                Статистика систем
              </a>
              <a
                className="btn secondary"
                href={authed ? cabinetTabHref(user, "dashboard") : PORTAL_LOGIN_HREF}
              >
                {authed ? "Мой кабинет" : "Создать кабинет"}
              </a>
            </div>
          </div>
        </section>
      </main>
      <PortalFooter />
    </>
  );
}
