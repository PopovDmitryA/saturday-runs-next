import { useEffect, useState } from "react";
import { FilterSelect } from "../../components/filters/FilterPanel";
import { RequireAuth } from "../../components/RequireAuth";
import {
  ApiError,
  getOrganizerEventDates,
  getOrganizerEventReport,
  type OrganizerEventDateItem,
  type SvodResponse,
  type SvodRunnerRow,
  type SvodVolunteerRow,
} from "../../lib/api";
import { formatDate, formatInt, platformCodeLabel } from "../../lib/format";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { locationHintFor } from "../../lib/locationHint";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { OrganizerBreadcrumbs } from "./OrganizerBreadcrumbs";
import { OrganizerDenied } from "./OrganizerDenied";
import "./organizer.css";

// Бейджи отметок — компактнее отдельных колонок с «да/нет», и в отчёт
// оргкоманды обычно уходят именно формулировки, а не крестики.
function runnerBadges(row: SvodRunnerRow): { label: string; variant: string }[] {
  const badges: { label: string; variant: string }[] = [];
  if (row.first_in_system) {
    badges.push({ label: "новичок системы", variant: "org-badge-new" });
  } else if (row.first_at_location) {
    badges.push({ label: "впервые здесь", variant: "org-badge-new" });
  }
  if (row.is_pb) {
    badges.push({ label: "личный рекорд", variant: "org-badge-pb" });
  } else if (row.is_location_pb) {
    badges.push({ label: "рекорд на этой локации", variant: "org-badge-pb" });
  }
  if (row.comeback) {
    badges.push({ label: "вернулся после паузы >года", variant: "org-badge-comeback" });
  }
  if (row.location_milestone) {
    badges.push({ label: `юбилей: ${row.location_milestone}-я здесь`, variant: "org-badge-jubilee" });
  }
  if (row.platform_milestone) {
    badges.push({
      label: `юбилей: ${row.platform_milestone}-я в системе`,
      variant: "org-badge-jubilee",
    });
  }
  if (row.location_next_milestone) {
    badges.push({
      label: `следующая — ${row.location_next_milestone}-я здесь`,
      variant: "org-badge-next",
    });
  }
  if (row.platform_next_milestone) {
    badges.push({
      label: `следующая — ${row.platform_next_milestone}-я в системе`,
      variant: "org-badge-next",
    });
  }
  return badges;
}

function volunteerBadges(row: SvodVolunteerRow): { label: string; variant: string }[] {
  const badges: { label: string; variant: string }[] = [];
  if (row.first_volunteering) {
    badges.push({ label: "первое волонтёрство", variant: "org-badge-new" });
  } else if (row.first_at_location) {
    badges.push({ label: "впервые волонтёрит здесь", variant: "org-badge-new" });
  }
  for (const role of row.new_roles) {
    badges.push({ label: `новая роль: ${role}`, variant: "org-badge-role" });
  }
  if (row.location_milestone) {
    badges.push({
      label: `юбилей: ${row.location_milestone}-е здесь`,
      variant: "org-badge-jubilee",
    });
  }
  if (row.platform_milestone) {
    badges.push({
      label: `юбилей: ${row.platform_milestone}-е в системе`,
      variant: "org-badge-jubilee",
    });
  }
  for (const role of row.roles) {
    if (role.milestone) {
      badges.push({
        label: `юбилей в роли «${role.label}»: ${role.milestone}-е`,
        variant: "org-badge-jubilee",
      });
    }
  }
  if (row.location_next_milestone) {
    badges.push({
      label: `следующее — ${row.location_next_milestone}-е здесь`,
      variant: "org-badge-next",
    });
  }
  return badges;
}

function Badges({ items }: { items: { label: string; variant: string }[] }) {
  if (items.length === 0) {
    return <span className="muted">—</span>;
  }
  return (
    <span className="org-badges">
      {items.map((badge) => (
        <span key={badge.label} className={`org-badge ${badge.variant}`}>
          {badge.label}
        </span>
      ))}
    </span>
  );
}

function eventOptionLabel(item: OrganizerEventDateItem): string {
  const number = item.event_number ? `№${item.event_number} · ` : "";
  return `${number}${formatDate(item.event_date)} · ${platformCodeLabel(item.platform_code)} · ${formatInt(item.finishers_count)} фин.`;
}

function OrganizerLocationContent({ slug }: { slug: string }) {
  const attachRunnersHead = useFloatingTableHead();
  const attachVolunteersHead = useFloatingTableHead();
  const [dates, setDates] = useState<OrganizerEventDateItem[] | null>(null);
  const [locationName, setLocationName] = useState<string | null>(null);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [report, setReport] = useState<SvodResponse | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
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
        if (payload.items.length > 0) {
          setSelectedEventId(payload.items[0].event_id);
        }
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
          setError(err instanceof Error ? err.message : "Не удалось загрузить события");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  useEffect(() => {
    if (!selectedEventId) {
      return;
    }
    let cancelled = false;
    setReportLoading(true);
    getOrganizerEventReport(slug, selectedEventId)
      .then((payload) => {
        if (!cancelled) {
          setReport(payload);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Не удалось загрузить свод");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setReportLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug, selectedEventId]);

  const name = locationName ?? locationHintFor(slug)?.name ?? null;
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

  return (
    <PortalSectionShell sidebar={sidebar}>
      <header className="loc-header">
        <OrganizerBreadcrumbs slug={slug} locationName={name} tool="Свод по пробежке" />
        <div className="loc-header-title">
          <h1>{name ?? "Локация"} — свод по пробежке</h1>
        </div>
        <p className="muted">
          Готовые отметки для отчёта оргкоманды: новички, гости, рекорды и юбилеи выбранного
          события.
        </p>
      </header>

      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!error && dates === null && <p className="muted">Загрузка…</p>}

      {!error && dates !== null && dates.length === 0 && (
        <div className="card">
          <p className="muted">У локации пока нет событий с протоколами.</p>
        </div>
      )}

      {!error && dates !== null && dates.length > 0 && (
        <>
          <section className="card org-toolbar-card">
            <div className="org-toolbar-row">
            <label className="org-toolbar-label">
              Событие{" "}
              <FilterSelect
                ariaLabel="Событие"
                value={selectedEventId ?? ""}
                onChange={(value) => setSelectedEventId(String(value))}
                options={dates.map((item) => ({ value: item.event_id, label: eventOptionLabel(item) }))}
              />
            </label>
            {/* Кнопка «Скачать .xlsx» убрана по решению Дмитрия 24.08.2026;
                эндпоинт экспорта на бэкенде остался. */}
            {/* Формирователь поста — отдельный инструмент хаба. */}
            <a className="btn btn-ghost btn-sm" href={`/organizer/${slug}/post`}>
              ✍️ Пост-отчёт →
            </a>
            </div>
          </section>

          {reportLoading && <p className="muted">Считаем свод…</p>}

          {!reportLoading && report && (
            <>
              {/* Каждая таблица — своя карточка: раньше «Бегуны» и «Волонтёры»
                  шли подряд и на прокрутке сливались в одну простыню. */}
              <section className="card org-table-card">
                <header className="org-table-head">
                  <h2 className="section-title">
                    <span className="org-table-emoji" aria-hidden="true">
                      🏃
                    </span>
                    Бегуны
                  </h2>
                  <span className="muted org-table-count">
                    {formatInt(report.event.finishers_count)} финишёров
                    {report.runners.length !== report.event.finishers_count
                      ? `, в своде ${formatInt(report.runners.length)} опознанных`
                      : ""}
                  </span>
                </header>
                <TableWrap stickyFirstCol innerRef={attachRunnersHead}>
                  <table className="data-table org-svod-table">
                    <thead>
                      <tr>
                        <th>Место</th>
                        <th>Имя</th>
                        <th>Время</th>
                        <th title="Возрастная группа в протоколе">Группа</th>
                        <th>Отметки для отчёта</th>
                        <th title="Пробежек на этой локации, включая эту">Здесь</th>
                        <th title="Пробежек в системе всего, включая эту">Всего</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.runners.map((row) => (
                        <tr key={`${row.participant_id}-${row.position}`}>
                          <td>{row.position ?? "—"}</td>
                          <td>
                            {row.name ?? "—"}
                          </td>
                          <td>{row.finish_time_display}</td>
                          <td className="org-nowrap">{row.age_group ?? "—"}</td>
                          <td>
                            <Badges items={runnerBadges(row)} />
                          </td>
                          <td>{formatInt(row.location_runs_count)}</td>
                          <td>{formatInt(row.platform_runs_count)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableWrap>
              </section>

              <section className="card org-table-card">
                <header className="org-table-head">
                  <h2 className="section-title">
                    <span className="org-table-emoji" aria-hidden="true">
                      🤝
                    </span>
                    Волонтёры
                  </h2>
                  <span className="muted org-table-count">
                    {formatInt(report.event.volunteers_count)} на старте
                  </span>
                </header>
                <TableWrap stickyFirstCol innerRef={attachVolunteersHead}>
                  <table className="data-table org-svod-table">
                    <thead>
                      <tr>
                        <th>Имя</th>
                        <th>Роли</th>
                        <th>Отметки для отчёта</th>
                        <th title="Волонтёрств на этой локации, включая это">Здесь</th>
                        <th title="Волонтёрств в системе всего, включая это">Всего</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.volunteers.map((row, index) => (
                        <tr key={row.participant_id ?? index}>
                          <td>
                            {row.name ?? "—"}
                          </td>
                          <td>
                            {row.roles
                              .map((role) => (role.count > 1 ? `${role.label} (${role.count})` : role.label))
                              .join(", ") || "—"}
                          </td>
                          <td>
                            <Badges items={volunteerBadges(row)} />
                          </td>
                          <td>{formatInt(row.location_vol_count)}</td>
                          <td>{formatInt(row.platform_vol_count)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableWrap>
              </section>
            </>
          )}
        </>
      )}
    </PortalSectionShell>
  );
}

export function OrganizerLocationPage({ slug }: { slug: string }) {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {() => <OrganizerLocationContent slug={slug} />}
    </RequireAuth>
  );
}
