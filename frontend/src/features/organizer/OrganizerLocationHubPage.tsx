import { useEffect, useState } from "react";
import { RequireAuth } from "../../components/RequireAuth";
import {
  ApiError,
  getOrganizerEventDates,
  type OrganizerEventDateItem,
} from "../../lib/api";
import { formatDate, formatInt } from "../../lib/format";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { locationHintFor, rememberLocationHint } from "../../lib/locationHint";
import { PortalSectionShell } from "../portal/PortalSectionShell";
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

  return (
    <PortalSectionShell sidebar={sidebar}>
      <header className="loc-header">
        <OrganizerBreadcrumbs slug={slug} locationName={name} />
        <div className="loc-header-title">
          <h1>{name ?? "Локация"} — кабинет организатора</h1>
        </div>
        <p className="muted">
          Расширенные данные площадки для оргкоманды. Публичная страница локации — рядом, эти
          таблицы видит только оргкоманда.
        </p>
      </header>

      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      <div className="org-hub-grid">
        <a className="card org-hub-card" href={`/organizer/${slug}/report`}>
          <span className="org-hub-emoji" aria-hidden="true">
            📋
          </span>
          <h2 className="org-hub-title">Свод по пробежке</h2>
          <p className="muted org-hub-text">
            Бегуны и волонтёры одного старта с готовыми отметками для отчёта: новички, гости,
            личные рекорды, юбилеи и новые роли. Выгружается в Excel.
          </p>
          <span className="org-hub-meta">
            {lastEvent
              ? `Последний старт: ${formatDate(lastEvent.event_date)} · ${formatInt(lastEvent.finishers_count)} финишёров`
              : dates === null
                ? "Загрузка…"
                : "Событий пока нет"}
          </span>
        </a>

        <a className="card org-hub-card" href={`/organizer/${slug}/absence`}>
          <span className="org-hub-emoji" aria-hidden="true">
            ⏳
          </span>
          <h2 className="org-hub-title">Долгая пауза</h2>
          <p className="muted org-hub-text">
            Постоянные участники площадки, которые давно не появлялись. Пауза меряется числом
            прошедших стартов локации, а не календарём.
          </p>
          <span className="org-hub-meta">
            {dates === null ? "Загрузка…" : `Стартов у локации: ${formatInt(dates.length)}`}
          </span>
        </a>
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
