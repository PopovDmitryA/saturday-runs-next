import { useEffect, useState } from "react";
import { RequireAuth } from "../../components/RequireAuth";
import {
  ApiError,
  getOrganizerEventDates,
  getOrganizerHealth,
  getOrganizerNewcomers,
  getOrganizerTeamLoad,
  type OrganizerEventDateItem,
  type OrganizerHealthResponse,
  type OrganizerNewcomersResponse,
} from "../../lib/api";
import { formatDate, formatInt } from "../../lib/format";
import { HeaderHint } from "../../components/tableUx/HeaderHint";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { locationHintFor, rememberLocationHint } from "../../lib/locationHint";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { DirectorRotationCard, type DirectorRotation } from "./DirectorRotationCard";
import { OrganizerBreadcrumbs } from "./OrganizerBreadcrumbs";
import { OrganizerDenied } from "./OrganizerDenied";
import "./organizer.css";

/**
 * Хаб инструментов локации — вход в кабинет (как главная «Рейтингов»):
 * сначала выбор таблицы, а не сразу простыня данных.
 */
function OrganizerHubContent({ slug }: { slug: string }) {
  const [dates, setDates] = useState<OrganizerEventDateItem[] | null>(null);
  const [locationName, setLocationName] = useState<string | null>(null);
  // Процент удержания — цифра на карточке (просьба Дмитрия 16.08.2026):
  // видно, как локация справляется с новичками, ещё до захода в таблицу.
  const [newcomers, setNewcomers] = useState<OrganizerNewcomersResponse | null>(null);
  // Светофор здоровья грузится отдельно и лениво: холодный расчёт сетевых
  // медиан занимает десятки секунд, хаб его не ждёт.
  const [health, setHealth] = useState<OrganizerHealthResponse | null>(null);
  const [healthFailed, setHealthFailed] = useState(false);
  // Ротация организаторов — та же карточка, что на «Команде и нагрузке»:
  // выгорание организатора закрывает площадку целиком, поэтому цифра нужна
  // на входе, а не через клик (просьба Дмитрия 03.09.2026).
  const [rotation, setRotation] = useState<DirectorRotation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getOrganizerEventDates(slug)
      .then((payload) => {
        if (cancelled) {
          return;
        }
        setDates(payload.items);
        setLocationName(payload.location.name);
        rememberLocationHint({ slug: payload.location.slug, name: payload.location.name });
        getOrganizerNewcomers(slug)
          .then((stats) => {
            if (!cancelled) {
              setNewcomers(stats);
            }
          })
          .catch(() => {
            // Тихо: карточка просто останется без процента.
          });
        getOrganizerHealth(slug)
          .then((stats) => {
            if (!cancelled) {
              setHealth(stats);
            }
          })
          .catch(() => {
            if (!cancelled) {
              setHealthFailed(true);
            }
          });
        getOrganizerTeamLoad(slug)
          .then((stats) => {
            if (!cancelled && stats.director_rotation) {
              setRotation(stats.director_rotation);
            }
          })
          .catch(() => {
            // Тихо: карточка просто не появится.
          });
      })
      .catch((err) => {
        if (cancelled) {
          return;
        }
        if (err instanceof ApiError && err.status === 403) {
          setForbidden(true);
        } else if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError(err instanceof Error ? err.message : "Не удалось загрузить локацию");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  const hintName = locationHintFor(slug)?.name ?? null;
  const name = locationName ?? hintName;
  const sidebar = {
    active: "organizer" as const,
    location: name ? { slug, name } : locationHintFor(slug),
  };

  if (forbidden || notFound) {
    return (
      <PortalSectionShell sidebar={sidebar}>
        <OrganizerDenied slug={slug} notFound={notFound} />
      </PortalSectionShell>
    );
  }

  const lastEvent = dates && dates.length > 0 ? dates[0] : null;
  // Пока даты не загрузились, считаем что 5в есть — карточка не мигает
  // дизейблом у обычных локаций.
  const hasFiveVerst = dates === null || dates.some((item) => item.platform_code === "five_verst");

  return (
    <PortalSectionShell sidebar={sidebar}>
      <header className="loc-header">
        <OrganizerBreadcrumbs slug={slug} locationName={name} />
        <div className="loc-header-title">
          <h1>{name ?? "Локация"} — кабинет организатора</h1>
        </div>
      </header>

      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {/* Светофор здоровья — над сеткой инструментов: это сводка, а не инструмент. */}
      <section className="card org-health-card">
        <header className="org-table-head">
          <h2 className="section-title">
            <span className="org-table-emoji" aria-hidden="true">
              🚦
            </span>
            Здоровье локации
          </h2>
        </header>
        {health === null && !healthFailed && <p className="muted">Считаем…</p>}
        {healthFailed && <p className="muted">Не удалось посчитать — обновите страницу позже.</p>}
        {health !== null && (
          <div className="org-health-grid">
            {health.indicators.map((indicator) => (
              <div key={indicator.key} className="org-health-item">
                <span
                  className={`org-health-dot org-health-${indicator.level ?? "none"}`}
                  aria-hidden="true"
                />
                <div className="org-health-text">
                  <span className="org-health-title">
                    {indicator.title}
                    {/* Подсказка: что это значит, и — где уместно — совет или
                        «на что влияет» (метка зашита в текст advice на бэке). */}
                    <HeaderHint
                      text={`${indicator.hint}${indicator.advice ? ` ${indicator.advice}` : ""}`}
                    />
                  </span>
                  <span className="muted org-health-value">
                    {indicator.value_display ?? "данных пока нет"}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {rotation && <DirectorRotationCard rotation={rotation} />}

      <div className="org-hub-grid">
        <a className="card org-hub-card" href={`/organizer/${slug}/report`}>
          <span className="org-hub-emoji" aria-hidden="true">
            📋
          </span>
          <h2 className="org-hub-title">Свод по пробежке</h2>
          <p className="muted org-hub-text">
            Бегуны и волонтёры одного старта с отметками для отчёта: новички, гости, рекорды,
            юбилеи и новые роли.
          </p>
          <span className="org-hub-meta">
            {lastEvent
              ? `Последний старт: ${formatDate(lastEvent.event_date)} · ${formatInt(lastEvent.finishers_count)} финишёров`
              : dates === null
                ? "Загрузка…"
                : "Событий пока нет"}
          </span>
        </a>

        <a className="card org-hub-card" href={`/organizer/${slug}/post`}>
          <span className="org-hub-emoji" aria-hidden="true">
            ✍️
          </span>
          <h2 className="org-hub-title">Пост-отчёт о пробежке</h2>
          <p className="muted org-hub-text">
            Готовый текст для чата или канала локации: цифры старта, топ финишей, новички,
            рекорды и юбилеи. Осталось скопировать и отправить.
          </p>
          <span className="org-hub-meta">Собирается по любому событию</span>
        </a>

        <a className="card org-hub-card" href={`/organizer/${slug}/milestones`}>
          <span className="org-hub-emoji" aria-hidden="true">
            🎉
          </span>
          <h2 className="org-hub-title">Календарь юбилеев</h2>
          <p className="muted org-hub-text">
            Кто из активных участников подходит к юбилейной пробежке или волонтёрству — чтобы
            подготовить поздравление заранее, а не узнать задним числом.
          </p>
          <span className="org-hub-meta">Юбилеи на ближайшие 4 участия</span>
        </a>

        <a className="card org-hub-card" href={`/organizer/${slug}/newcomers`}>
          <span className="org-hub-emoji" aria-hidden="true">
            🌱
          </span>
          <h2 className="org-hub-title">Удержание новичков</h2>
          <p className="muted org-hub-text">
            Кто впервые в жизни пробежал именно здесь — и вернулся ли. Гости с других локаций
            новичками не считаются.
          </p>
          <span className="org-hub-meta">
            {newcomers === null
              ? "Считаем…"
              : newcomers.retention_pct === null
                ? "Дебютов за полгода не было"
                : `Вернулись сюда: ${newcomers.retention_pct}% новичков за полгода`}
          </span>
        </a>

        <a className="card org-hub-card" href={`/organizer/${slug}/volunteers`}>
          <span className="org-hub-emoji" aria-hidden="true">
            🤝
          </span>
          <h2 className="org-hub-title">Волонтёрская скамейка</h2>
          <p className="muted org-hub-text">
            Кого позвать в оргкоманду: кто регулярно бегает здесь, но ни разу не волонтёрил, и
            кто выпал из команды, продолжая бегать.
          </p>
          <span className="org-hub-meta">Кандидаты — в начале списка</span>
        </a>

        <a className="card org-hub-card" href={`/organizer/${slug}/team`}>
          <span className="org-hub-emoji" aria-hidden="true">
            🧯
          </span>
          <h2 className="org-hub-title">Команда и нагрузка</h2>
          <p className="muted org-hub-text">
            Какие ключевые роли держатся на одном-двух людях, кто тянет больше всех и как наша
            ротация выглядит на фоне системы.
          </p>
          <span className="org-hub-meta">Bus-фактор каждой роли</span>
        </a>

        <a className="card org-hub-card" href={`/organizer/${slug}/attendance`}>
          <span className="org-hub-emoji" aria-hidden="true">
            📈
          </span>
          <h2 className="org-hub-title">Посещаемость</h2>
          <p className="muted org-hub-text">
            Растём или падаем: среднее число финишёров по месяцам, сравнение год к году и рекорд
            локации.
          </p>
          <span className="org-hub-meta">График за всю историю локации</span>
        </a>

        <a className="card org-hub-card" href={`/organizer/${slug}/audience`}>
          <span className="org-hub-emoji" aria-hidden="true">
            🧑‍🤝‍🧑
          </span>
          <h2 className="org-hub-title">Портрет участника</h2>
          <p className="muted org-hub-text">
            Кто к нам ходит: возраст, пол и клубы участников — и сравнение локации с соседями по
            городу, региону и всей системе.
          </p>
          <span className="org-hub-meta">Плюс «мы и соседи»</span>
        </a>

        {/* Наблюдатель выгрузки умеет только 5verst.ru: локациям других систем
            карточка дизейблится с объяснением (решение Дмитрия 24.08.2026). */}
        {hasFiveVerst ? (
          <a className="card org-hub-card" href={`/organizer/${slug}/protocols`}>
            <span className="org-hub-emoji" aria-hidden="true">
              ⏱
            </span>
            <h2 className="org-hub-title">Протоколы</h2>
            <p className="muted org-hub-text">
              Как быстро протокол появляется на 5 вёрст: задержка от финиша последнего участника,
              организатор дня и журнал правок по каждому старту.
            </p>
            <span className="org-hub-meta">История наблюдения — с августа 2024</span>
          </a>
        ) : (
          <div className="card org-hub-card org-hub-card-disabled" aria-disabled="true">
            <span className="org-hub-emoji" aria-hidden="true">
              ⏱
            </span>
            <h2 className="org-hub-title">Протоколы</h2>
            <p className="muted org-hub-text">
              Скорость выгрузки протоколов отслеживается только у стартов 5 вёрст.
            </p>
            <span className="org-hub-soon">Только 5 вёрст</span>
          </div>
        )}

        <a className="card org-hub-card" href={`/organizer/${slug}/absence`}>
          <span className="org-hub-emoji" aria-hidden="true">
            ⏳
          </span>
          <h2 className="org-hub-title">Долгая пауза</h2>
          <p className="muted org-hub-text">
            Постоянные участники локации, которые давно не появлялись. Пауза меряется числом
            прошедших стартов локации, а не календарём.
          </p>
          <span className="org-hub-meta">
            {dates === null ? "Загрузка…" : `Стартов у локации: ${formatInt(dates.length)}`}
          </span>
        </a>

        <div className="card org-hub-card org-hub-card-disabled" aria-disabled="true">
          <span className="org-hub-emoji" aria-hidden="true">
            ⭐
          </span>
          <h2 className="org-hub-title">Отзывы и оценки</h2>
          <p className="muted org-hub-text">
            Что участники говорят о вашей локации: оценки организации, трассы и атмосферы с
            комментариями. Уже копим данные — а пока рекомендуйте участникам оставить оценку на
            странице локации: это ваша будущая обратная связь.
          </p>
          <span className="org-hub-soon">Скоро</span>
        </div>
      </div>

      <div className="card org-hub-footnote">
        <p className="muted">
          Нужен ещё какой-то срез по локации — напишите в{" "}
          <a href="/backlog">бэклог</a>: раздел только начали, инструменты будем добавлять.
        </p>
      </div>
    </PortalSectionShell>
  );
}

export function OrganizerLocationHubPage({ slug }: { slug: string }) {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {() => <OrganizerHubContent slug={slug} />}
    </RequireAuth>
  );
}
