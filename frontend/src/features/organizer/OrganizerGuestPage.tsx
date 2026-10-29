import { useMemo, type ReactNode } from "react";
import { RequireAuth } from "../../components/RequireAuth";
import { useCachedResource } from "../../hooks/useCachedResource";
import { COUNT_FORMS, formatDateLong, formatDuration, formatInt, pluralFormRu } from "../../lib/format";
import { locationHintFor } from "../../lib/locationHint";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { planDayLabel } from "../planner/PlanModal";
import { PlanFirstHereTag } from "../planner/PlanPeople";
import { getOrganizerGuest, type GuestChallenge, type OrganizerGuestCard } from "../planner/plannerApi";
import { seriesSpanText } from "../planner/seriesText";
import { GUESTS_TOOL_LABEL, GuestHomePlace, listedGuestHint, organizerGuestsHref } from "./guestParts";
import { OrganizerBreadcrumbs } from "./OrganizerBreadcrumbs";
import { OrganizerDenied } from "./OrganizerDenied";
import "../planner/planner.css";
import "./organizer.css";
import "./organizerGuests.css";

/** «18:08»: часовых времён на пятёрке не бывает. */
function finishTime(sec: number): string {
  return formatDuration(sec).replace(/^00:/, "");
}

function StatTile({ value, forms }: { value: number | null; forms: readonly [string, string, string] }) {
  return (
    <div className="stat-card org-guest-stat">
      <span className="stat-value">{value != null ? formatInt(value) : "—"}</span>
      {/* Нет числа — подпись во множественном: «— пробежек». */}
      <span className="stat-label">{pluralFormRu(value ?? 0, forms)}</span>
    </div>
  );
}

/** Строка «Пробежек здесь — 5»: подпись слева, значение справа. */
function FactRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="org-guest-fact">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function HereCard({ card }: { card: OrganizerGuestCard }) {
  const { stats } = card;
  return (
    <section className="card org-guest-section" aria-labelledby="org-guest-here">
      <h2 className="section-title" id="org-guest-here">
        На вашей локации
      </h2>
      {stats.first_here ? (
        <p className="org-guest-lead">
          <PlanFirstHereTag /> Ни пробежки, ни волонтёрства у вас — приедет впервые.
        </p>
      ) : (
        <dl className="org-guest-facts">
          <FactRow label="Пробежек здесь">{formatInt(stats.runs_here ?? 0)}</FactRow>
          <FactRow label="Волонтёрств здесь">{formatInt(stats.volunteering_here ?? 0)}</FactRow>
          <FactRow label="Последний раз">{stats.last_here ? formatDateLong(stats.last_here) : "—"}</FactRow>
          <FactRow label="Лучшее время здесь">
            {stats.best_here_sec != null ? finishTime(stats.best_here_sec) : "—"}
          </FactRow>
        </dl>
      )}
    </section>
  );
}

function HomeCard({ card }: { card: OrganizerGuestCard }) {
  const { home } = card;
  const city = home?.city?.trim() || null;
  return (
    <section className="card org-guest-section" aria-labelledby="org-guest-home">
      <h2 className="section-title" id="org-guest-home">
        Откуда
      </h2>
      {home ? (
        <dl className="org-guest-facts">
          <FactRow label="Домашняя локация">
            {/* Город — своей строкой ниже, не после названия. */}
            <GuestHomePlace home={{ ...home, city: null }} />
          </FactRow>
          {city && <FactRow label="Город">{city}</FactRow>}
        </dl>
      ) : (
        <p className="muted org-guest-lead">Домашняя локация не определена.</p>
      )}
      <p className="muted org-guest-note">
        Домашняя — та, что человек выбрал в профиле, а если не выбирал — где он бегает чаще всего.
      </p>
    </section>
  );
}

/**
 * Юбилеи — как их посчитал сервер: только на три ближайших старта и с учётом
 * других планов человека. План дальше горизонта — пояснение под списком
 * (jubilees_note): иначе «юбилеев нет» читалось бы как «и не будет».
 */
function JubileesCard({ card }: { card: OrganizerGuestCard }) {
  const note = card.jubilees_note?.trim() || null;
  return (
    <section className="card org-guest-section" aria-labelledby="org-guest-jubilees">
      <h2 className="section-title" id="org-guest-jubilees">
        Юбилеи
      </h2>
      {card.jubilees.length > 0 ? (
        <ul className="org-guest-list">
          {card.jubilees.map((item) => (
            <li key={item.label}>
              <span aria-hidden="true">🎉 </span>
              {item.label}
            </li>
          ))}
        </ul>
      ) : (
        !note && <p className="muted org-guest-lead">Ближайших юбилеев нет.</p>
      )}
      {note && <p className="muted org-guest-note">{note}</p>}
    </section>
  );
}

function ChallengeList({ items }: { items: GuestChallenge[] }) {
  return (
    <ul className="org-guest-list org-guest-challenges">
      {items.map((item) => (
        <li key={`${item.title}-${item.text}`}>
          <strong>{item.title}</strong>
          <span className="muted">{item.text}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * Ближайший визит — под заголовком, следующие даты сюда (в горизонте трёх
 * ближайших стартов) — каждая своей подписью: у каждой даты в «Кто у нас
 * бежит» своя строка в «Челленджах», и страница, открытая из второй
 * субботы, говорит то же.
 */
function ChallengesCard({ card }: { card: OrganizerGuestCard }) {
  // Дата — того визита, о котором блок: сегодняшний, уже лёгший в протокол,
  // ничего не даст, и сервер говорит о следующем.
  const next = card.challenges_date;
  const note = card.challenges_note?.trim() || null;
  return (
    <section className="card org-guest-section" aria-labelledby="org-guest-challenges">
      <div className="org-guest-section-head">
        <h2 className="section-title" id="org-guest-challenges">
          Что даст визит
        </h2>
        {next && <span className="muted org-guest-note">{planDayLabel(next)}</span>}
      </div>
      {next == null ? (
        <p className="muted org-guest-lead">Запланированных визитов впереди нет.</p>
      ) : card.challenges.length > 0 ? (
        <ChallengeList items={card.challenges} />
      ) : (
        !note && <p className="muted org-guest-lead">Нового по челленджам сайта этот визит не даст.</p>
      )}
      {note && <p className="muted org-guest-note">{note}</p>}
      {(card.later_visits ?? []).map((visit) => (
        <div key={visit.date} className="org-guest-later-visit">
          <h3 className="org-guest-later-date">{planDayLabel(visit.date)}</h3>
          <ChallengeList items={visit.challenges} />
        </div>
      ))}
    </section>
  );
}

function GuestUnavailable({ slug }: { slug: string }) {
  return (
    <div className="card">
      <h2 className="section-title">Страница участника недоступна</h2>
      <p>
        Она открыта, пока человек публично планирует пробежать у вас. Возможно, он убрал план, сделал его
        приватным или закрыл профиль.
      </p>
      <p>
        <a className="btn secondary btn-sm" href={organizerGuestsHref(slug)}>
          {GUESTS_TOOL_LABEL}
        </a>
      </p>
    </div>
  );
}

/**
 * Профиль в системах не привязан: пробежек на сайте у человека нет, и «здесь
 * 0», «юбилеев нет», «нового не даст» читались бы как факты о нём. Под
 * непривязанным штрихкодом он может быть вашим завсегдатаем.
 */
function UnlinkedCard() {
  return (
    <section className="card org-guest-section" aria-labelledby="org-guest-unlinked">
      <h2 className="section-title" id="org-guest-unlinked">
        На вашей локации
      </h2>
      <p className="muted org-guest-lead">
        Профиль в системах не привязан — статистики на сайте нет: ни пробежек здесь, ни юбилеев, ни челленджей.
      </p>
    </section>
  );
}

/**
 * Страница участника в оргкабинете: что важно знать организатору о человеке,
 * который планирует у них пробежать, — пробежки и волонтёрства, бывал ли
 * здесь, откуда, юбилеи и что даст визит по челленджам. Открыта, только пока у человека есть
 * будущий публичный план на эту локацию: смотреть так произвольных людей
 * организатор не может (сервер отвечает 404).
 */
function OrganizerGuestContent({ slug, handle }: { slug: string; handle: string }) {
  const { data, loading, error, forbidden, notFound } = useCachedResource(
    `planner:organizer-guest:${slug}:${handle}`,
    () => getOrganizerGuest(slug, handle),
    [slug, handle],
    { errorText: "Не удалось загрузить страницу участника" },
  );

  // Из списка «Кто у нас бежит», если пришли из него: имя — в заголовок, пока
  // едет ответ, название локации — в крошки, даже когда ответ — 404.
  const listed = useMemo(() => listedGuestHint(slug, handle), [slug, handle]);

  const name = data?.location.name ?? listed?.locationName ?? locationHintFor(slug)?.name ?? null;
  const sidebar = {
    active: "organizer" as const,
    location: name ? { slug, name } : locationHintFor(slug),
  };

  if (forbidden) {
    return (
      <PortalSectionShell sidebar={sidebar}>
        <OrganizerDenied slug={slug} notFound={false} />
      </PortalSectionShell>
    );
  }

  // 404 — страница закрыта (план убран или стал приватным): ответ из кэша
  // «назад» и имя из списка тут уже не показываем.
  const person = notFound ? null : (data?.person ?? null);
  const personName = person?.name ?? (notFound ? null : listed?.name) ?? "Участник";

  // ym-hide-content — вебвизор Метрики не записывает имя и цифры человека:
  // по общему согласию (пункт о планах) их видят только организаторы (адрес
  // без хендла — metrikaPath).
  return (
    <PortalSectionShell sidebar={sidebar}>
      <header className="loc-header ym-hide-content">
        <OrganizerBreadcrumbs
          slug={slug}
          locationName={name}
          tool={GUESTS_TOOL_LABEL}
          toolHref={organizerGuestsHref(slug)}
          leaf={personName}
        />
        <div className="loc-header-title">
          <h1>{personName}</h1>
        </div>
        {person && (
          <p className="org-guest-profile">
            <a href={`/users/${encodeURIComponent(person.handle)}`}>Профиль на сайте</a>
          </p>
        )}
      </header>

      {notFound && <GuestUnavailable slug={slug} />}

      {error && !data && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {loading && !data && <p className="muted">Загрузка…</p>}

      {data && !notFound && (
        <div className="org-guest-page ym-hide-content">
          <section className="card org-guest-section" aria-labelledby="org-guest-plans">
            <h2 className="section-title" id="org-guest-plans">
              Планирует пробежать у вас
            </h2>
            {data.series && (
              <p className="org-guest-series">
                <span aria-hidden="true">↻ </span>
                Каждую субботу {seriesSpanText(data.series)}
              </p>
            )}
            <ul className="org-guest-dates">
              {data.planned_dates.map((item) => (
                <li
                  key={item.date}
                  className={item.kind === "special" ? "org-guest-date org-guest-date-special" : "org-guest-date"}
                >
                  {/* Дата и название особого старта — каждое целиком: на телефоне
                      чип переносится между ними, а не отрывает «старт» на вторую строку. */}
                  <span className="org-guest-date-part">
                    {item.kind === "special" && <span aria-hidden="true">★ </span>}
                    {planDayLabel(item.date, true)}
                  </span>
                  {item.kind === "special" && (
                    <>
                      {" · "}
                      <span className="org-guest-date-part">{item.title || "особый старт"}</span>
                    </>
                  )}
                </li>
              ))}
            </ul>
          </section>

          <div className="org-guest-stats" aria-label="Всего по всем системам">
            <StatTile value={data.stats.runs} forms={COUNT_FORMS.runs} />
            <StatTile value={data.stats.volunteering} forms={COUNT_FORMS.volunteering} />
            <StatTile value={data.stats.locations} forms={COUNT_FORMS.locations} />
          </div>

          <div className="org-guest-grid">
            {data.stats.runs != null ? <HereCard card={data} /> : <UnlinkedCard />}
            <HomeCard card={data} />
            {data.stats.runs != null && <JubileesCard card={data} />}
            {data.stats.runs != null && <ChallengesCard card={data} />}
          </div>
        </div>
      )}
    </PortalSectionShell>
  );
}

export function OrganizerGuestPage({ slug, handle }: { slug: string; handle: string }) {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {() => <OrganizerGuestContent slug={slug} handle={handle} />}
    </RequireAuth>
  );
}
