import { useEffect, useMemo, useState } from "react";
import { FilterSelect } from "../../components/filters/FilterPanel";
import { RequireAuth } from "../../components/RequireAuth";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import {
  ApiError,
  getOrganizerNewcomers,
  type OrganizerNewcomerItem,
  type OrganizerNewcomersResponse,
} from "../../lib/api";
import { formatInt, pluralizeRu } from "../../lib/format";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { locationHintFor } from "../../lib/locationHint";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { useFloatingTableHead } from "../../lib/useFloatingTableHead";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { OrganizerBreadcrumbs } from "./OrganizerBreadcrumbs";
import { OrganizerDenied } from "./OrganizerDenied";
import "./organizer.css";

const PERIOD_OPTIONS = [
  { days: 90, label: "3 месяца" },
  { days: 180, label: "6 месяцев" },
  { days: 365, label: "год" },
];

type SortKey = "name" | "debut" | "returned" | "runs_here" | "runs_elsewhere" | "last_anywhere";
type SortState = { key: SortKey; asc: boolean };

function sortValue(row: OrganizerNewcomerItem, key: SortKey): number | string {
  switch (key) {
    case "name":
      return row.name ?? "";
    case "debut":
      return row.debut_date ?? "";
    case "returned":
      // «вернулся сюда» > «бегает в другом месте» > «пока нет».
      return row.returned_here ? 2 : row.runs_elsewhere > 0 ? 1 : 0;
    case "runs_here":
      return row.runs_here;
    case "runs_elsewhere":
      return row.runs_elsewhere;
    case "last_anywhere":
      // В ответе только дд.мм.гггг — разворачиваем в гггг-мм-дд для сортировки.
      return row.last_anywhere_display ? row.last_anywhere_display.split(".").reverse().join("") : "";
  }
}

function OrganizerNewcomersContent({ slug }: { slug: string }) {
  const attachFloatingHead = useFloatingTableHead();
  const [days, setDays] = useState(180);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortState | null>(null);
  const [data, setData] = useState<OrganizerNewcomersResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getOrganizerNewcomers(slug, days)
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
          setError(err instanceof Error ? err.message : "Не удалось загрузить новичков");
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
  }, [slug, days]);

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const items = (data?.items ?? []).filter(
      (item) => !needle || (item.name ?? "").toLowerCase().includes(needle),
    );
    if (sort) {
      items.sort((a, b) => {
        const left = sortValue(a, sort.key);
        const right = sortValue(b, sort.key);
        if (left === right) {
          return (a.name ?? "") < (b.name ?? "") ? -1 : 1;
        }
        const compare = left < right ? -1 : 1;
        return sort.asc ? compare : -compare;
      });
    }
    return items;
  }, [data, query, sort]);

  const toggleSort = (key: SortKey) => {
    setSort((current) =>
      current && current.key === key ? { key, asc: !current.asc } : { key, asc: key === "name" },
    );
  };

  const sortProps = (key: SortKey) => ({
    filterable: false,
    sortActive: sort?.key === key,
    sortAsc: sort?.asc ?? false,
    onSort: () => toggleSort(key),
  });

  const name = data?.location.name ?? locationHintFor(slug)?.name ?? null;
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
        <OrganizerBreadcrumbs slug={slug} locationName={name} tool="Удержание новичков" />
        <div className="loc-header-title">
          <h1>{name ?? "Локация"} — удержание новичков</h1>
        </div>
        <p className="muted">
          Новичок — тот, чья первая пробежка в системе случилась на этой локации (гости
          с других локаций не считаются). Смотрим, вернулся ли он сюда ещё раз.
        </p>
      </header>

      <section className="card org-toolbar-card">
        <div className="org-toolbar-row">
          <label className="org-toolbar-label">
            Дебюты за{" "}
            <FilterSelect
              ariaLabel="Дебюты за период"
              value={days}
              onChange={setDays}
              options={PERIOD_OPTIONS.map((option) => ({ value: option.days, label: option.label }))}
            />
          </label>
          <input
            className="input org-newcomers-search"
            type="search"
            placeholder="Поиск по имени или фамилии…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          {data && (
            <span className="muted">
              Новичков: {formatInt(data.total)}
              {data.retention_pct !== null && (
                <>
                  {" "}
                  · вернулись сюда: <strong>{data.retention_pct}%</strong> (
                  {formatInt(data.returned_here_total)} из {formatInt(data.eligible_total)}, без
                  дебютантов последнего старта)
                </>
              )}
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

      {!loading && !error && data && data.items.length === 0 && (
        <div className="card">
          <p className="muted">За выбранный период дебютов на локации не было.</p>
        </div>
      )}

      {!loading && !error && data && rows.length > 0 && (
        <section className="card org-table-card">
          <TableWrap stickyFirstCol innerRef={attachFloatingHead}>
            <table className="data-table org-svod-table">
              <thead>
                <tr>
                  <ColumnHeader label="Имя" {...sortProps("name")} />
                  <ColumnHeader
                    label="Дебют"
                    hint="Дата первой пробежки в системе — она же дебют здесь"
                    {...sortProps("debut")}
                  />
                  <ColumnHeader label="Вернулся сюда" {...sortProps("returned")} />
                  <ColumnHeader
                    label="Здесь"
                    hint="Пробежек на этой локации, включая дебют"
                    {...sortProps("runs_here")}
                  />
                  <ColumnHeader
                    label="В других местах"
                    hint="Пробежек на других локациях после дебюта"
                    {...sortProps("runs_elsewhere")}
                  />
                  <ColumnHeader
                    label="Последний старт"
                    hint="Последняя пробежка где угодно"
                    {...sortProps("last_anywhere")}
                  />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.participant_id}>
                    <td>
                      {row.name ?? "—"}
                    </td>
                    <td>{row.debut_date_display}</td>
                    <td>
                      {row.returned_here ? (
                        <span className="org-badge org-badge-new">
                          да, {pluralizeRu(row.runs_here, ["старт", "старта", "стартов"])}
                        </span>
                      ) : row.runs_elsewhere > 0 ? (
                        <span className="org-badge org-badge-comeback">бегает в другом месте</span>
                      ) : (
                        <span className="muted">пока нет</span>
                      )}
                    </td>
                    <td>{formatInt(row.runs_here)}</td>
                    <td>{row.runs_elsewhere > 0 ? formatInt(row.runs_elsewhere) : "—"}</td>
                    <td>{row.last_anywhere_display ?? "—"}</td>
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

export function OrganizerNewcomersPage({ slug }: { slug: string }) {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {() => <OrganizerNewcomersContent slug={slug} />}
    </RequireAuth>
  );
}
