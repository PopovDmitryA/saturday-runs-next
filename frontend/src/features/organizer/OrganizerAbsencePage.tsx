import { useEffect, useMemo, useState } from "react";
import { FilterSelect } from "../../components/filters/FilterPanel";
import { RequireAuth } from "../../components/RequireAuth";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import {
  ApiError,
  getOrganizerAbsence,
  type OrganizerAbsenceItem,
  type OrganizerAbsenceResponse,
} from "../../lib/api";
import { formatInt } from "../../lib/format";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { locationHintFor } from "../../lib/locationHint";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { OrganizerBreadcrumbs } from "./OrganizerBreadcrumbs";
import { OrganizerDenied } from "./OrganizerDenied";
import "./organizer.css";

// Пороги — как в легаси-дашборде «Долгая пауза».
const MIN_RUNS_OPTIONS = [5, 10, 15, 20, 25, 30];
const MIN_MISSED_OPTIONS = [1, 2, 3, 4, 6, 8, 10, 15, 20];

type SortKey = "runs_here" | "runs_total" | "missed" | "last_date" | "name";
type SortState = { key: SortKey; asc: boolean };

function sortValue(row: OrganizerAbsenceItem, key: SortKey): number | string | null {
  switch (key) {
    case "runs_here":
      return row.runs_here;
    case "runs_total":
      return row.runs_total;
    case "missed":
      return row.missed_events;
    case "last_date":
      return row.last_date;
    case "name":
      return row.name ?? "";
  }
}

function OrganizerAbsenceContent({ slug }: { slug: string }) {
  const attachFloatingHead = useFloatingTableHead();
  const [minRuns, setMinRuns] = useState(10);
  const [minMissed, setMinMissed] = useState(4);
  const [data, setData] = useState<OrganizerAbsenceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [sort, setSort] = useState<SortState>({ key: "runs_here", asc: false });

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getOrganizerAbsence(slug, minRuns, minMissed)
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
          setError(null);
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
          setError(err instanceof Error ? err.message : "Не удалось загрузить данные");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug, minRuns, minMissed]);

  const rows = useMemo(() => {
    const items = [...(data?.items ?? [])];
    items.sort((a, b) => {
      const left = sortValue(a, sort.key);
      const right = sortValue(b, sort.key);
      if (left === right) {
        return (a.name ?? "") < (b.name ?? "") ? -1 : 1;
      }
      if (left === null) {
        return 1;
      }
      if (right === null) {
        return -1;
      }
      const compare = left < right ? -1 : 1;
      return sort.asc ? compare : -compare;
    });
    return items;
  }, [data, sort]);

  const toggleSort = (key: SortKey) => {
    setSort((current) =>
      current.key === key ? { key, asc: !current.asc } : { key, asc: key === "name" },
    );
  };

  const sortProps = (key: SortKey) => ({
    filterable: false,
    sortActive: sort.key === key,
    sortAsc: sort.asc,
    onSort: () => toggleSort(key),
  });

  const name = data?.location.name ?? locationHintFor(slug)?.name ?? null;
  const shellSidebar = {
    active: "organizer" as const,
    location: name ? { slug, name } : locationHintFor(slug),
  };

  if (forbidden || notFound) {
    return (
      <PortalSectionShell sidebar={shellSidebar}>
        <OrganizerDenied slug={slug} notFound={notFound} />
      </PortalSectionShell>
    );
  }

  return (
    <PortalSectionShell sidebar={shellSidebar}>
      <header className="loc-header">
        <OrganizerBreadcrumbs slug={slug} locationName={name} tool="Долгая пауза" />
        <div className="loc-header-title">
          <h1>{name ?? "Локация"} — долгая пауза</h1>
        </div>
        <p className="muted">
          Постоянные участники локации, которые давно не появлялись: пауза меряется числом
          прошедших событий локации, а не календарём.
        </p>
      </header>

      <section className="card org-toolbar-card">
        <div className="org-toolbar-row">
        <label className="org-toolbar-label">
          Пробежек на локации от{" "}
          <FilterSelect
            ariaLabel="Пробежек на локации от"
            value={minRuns}
            onChange={setMinRuns}
            options={MIN_RUNS_OPTIONS.map((value) => ({ value, label: String(value) }))}
          />
        </label>
        <label className="org-toolbar-label">
          Пропущено событий от{" "}
          <FilterSelect
            ariaLabel="Пропущено событий от"
            value={minMissed}
            onChange={setMinMissed}
            options={MIN_MISSED_OPTIONS.map((value) => ({ value, label: String(value) }))}
          />
        </label>
        {data && (
          <span className="muted">
            Найдено: {formatInt(data.total)} · событий у локации: {formatInt(data.events_total)}
          </span>
        )}
        </div>
      </section>

      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {loading && <p className="muted">Загрузка…</p>}

      {!loading && !error && data && rows.length === 0 && (
        <div className="card">
          <p className="muted">
            Никто не подходит под пороги — либо все постоянные участники на месте, либо стоит
            ослабить фильтры.
          </p>
        </div>
      )}

      {!loading && !error && rows.length > 0 && (
        <section className="card org-table-card">
          <TableWrap stickyFirstCol innerRef={attachFloatingHead}>
            <table className="data-table org-svod-table">
              <thead>
                <tr>
                  <ColumnHeader label="Имя" {...sortProps("name")} />
                  <ColumnHeader
                    label="Последний визит"
                    hint="Дата последней пробежки на этой локации"
                    {...sortProps("last_date")}
                  />
                  <ColumnHeader
                    label="Пропущено"
                    hint="Сколько событий локации прошло после последнего визита"
                    {...sortProps("missed")}
                  />
                  <ColumnHeader
                    label="Здесь"
                    hint="Пробежек на этой локации"
                    {...sortProps("runs_here")}
                  />
                  <ColumnHeader
                    label="Всего"
                    hint="Пробежек всего (все локации, без тестовых)"
                    {...sortProps("runs_total")}
                  />
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr key={`${row.name}-${row.last_date}-${index}`}>
                    <td>
                      {row.handle ? (
                        <a href={`/users/${row.handle}`} target="_blank" rel="noreferrer">
                          {row.name ?? "—"}
                        </a>
                      ) : (
                        row.name ?? "—"
                      )}
                    </td>
                    <td>{row.last_date_display}</td>
                    <td>{formatInt(row.missed_events)}</td>
                    <td>{formatInt(row.runs_here)}</td>
                    <td>{formatInt(row.runs_total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </section>
      )}
    </PortalSectionShell>
  );
}

export function OrganizerAbsencePage({ slug }: { slug: string }) {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {() => <OrganizerAbsenceContent slug={slug} />}
    </RequireAuth>
  );
}
