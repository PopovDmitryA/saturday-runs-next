import { useEffect, useMemo, useState } from "react";
import { FilterSelect } from "../../components/filters/FilterPanel";
import { RequireAuth } from "../../components/RequireAuth";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import {
  ApiError,
  getOrganizerVolunteerBench,
  type OrganizerBenchItem,
  type OrganizerBenchResponse,
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

// Что за человек в списке. Порядок сегментов — от «кого звать» к «кто в строю»:
// главная ценность скамейки не в заслугах ветеранов, а в подсказке, кого
// пригласить (переосмысление 18.08.2026).
type Segment = "candidates" | "never" | "paused" | "active" | "all";

const SEGMENTS: { key: Segment; label: string; hint: string }[] = [
  {
    key: "candidates",
    label: "Кого позвать",
    hint: "Бегают здесь и сейчас, но в оргкоманде их нет",
  },
  {
    key: "never",
    label: "Ни разу не волонтёрил",
    hint: "Бегают на локации, но ни одного волонтёрства здесь",
  },
  {
    key: "paused",
    label: "Давно не волонтёрили",
    hint: "Волонтёрили когда-то, но давно не выходили",
  },
  { key: "active", label: "В строю", hint: "Выходили волонтёрить недавно" },
  { key: "all", label: "Все", hint: "Весь список целиком" },
];

const MIN_RUNS_OPTIONS = [3, 5, 10, 20];

// Порог давности: людей, пропавших из системы совсем, звать некого — по
// умолчанию показываем появлявшихся (пробежка или волонтёрство) за год.
const ACTIVITY_OPTIONS = [
  { months: 3, label: "3 месяца" },
  { months: 6, label: "полгода" },
  { months: 12, label: "год" },
  { months: 0, label: "всё время" },
];

function lastSeenIso(row: OrganizerBenchItem): string {
  const dates = [row.last_run_date, row.last_vol_date].filter(Boolean) as string[];
  return dates.sort().pop() ?? "";
}

/** Роли: первые три сразу, остальное — по клику «ещё N». */
function RolesCell({ roles }: { roles: OrganizerBenchItem["roles"] }) {
  const [expanded, setExpanded] = useState(false);
  if (roles.length === 0) {
    return <span className="muted">—</span>;
  }
  const fmt = (role: OrganizerBenchItem["roles"][number]) =>
    role.count > 1 ? `${role.label} (${role.count})` : role.label;
  const visible = expanded ? roles : roles.slice(0, 3);
  const hidden = roles.length - 3;
  return (
    <span>
      {visible.map(fmt).join(", ")}
      {!expanded && hidden > 0 && (
        <>
          {" "}
          <button type="button" className="org-roles-more" onClick={() => setExpanded(true)}>
            ещё {hidden}
          </button>
        </>
      )}
    </span>
  );
}

type SortKey =
  | "name"
  | "runs_here"
  | "runs_after"
  | "vols_here"
  | "vols_total"
  | "last_vol"
  | "last_run"
  | "missed";
type SortState = { key: SortKey; asc: boolean };

function sortValue(row: OrganizerBenchItem, key: SortKey): number | string {
  switch (key) {
    case "name":
      return row.name ?? "";
    case "runs_here":
      return row.runs_here;
    case "runs_after":
      return row.runs_after_last_vol;
    case "vols_here":
      return row.vols_here;
    case "vols_total":
      return row.vols_total;
    case "last_vol":
      return row.last_vol_date ?? "";
    case "last_run":
      return row.last_run_date ?? "";
    case "missed":
      return row.missed_events ?? -1;
  }
}

function StatusBadge({ row }: { row: OrganizerBenchItem }) {
  if (row.status === "never") {
    return (
      <span className="org-badge org-badge-new" title="Волонтёрств на этой локации нет">
        никогда
      </span>
    );
  }
  if (row.status === "paused") {
    return (
      <span
        className="org-badge org-badge-pb"
        title={`Последнее волонтёрство ${row.last_vol_display}, пропущено событий: ${row.missed_events}`}
      >
        давно
      </span>
    );
  }
  return (
    <span
      className="org-badge org-badge-role"
      title={`Последнее волонтёрство ${row.last_vol_display}`}
    >
      в строю
    </span>
  );
}

function OrganizerBenchContent({ slug }: { slug: string }) {
  // Плавающая шапка: на длинном списке заголовки колонок не уезжают.
  const attachFloatingHead = useFloatingTableHead();
  const [minRuns, setMinRuns] = useState(5);
  const [data, setData] = useState<OrganizerBenchResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [segment, setSegment] = useState<Segment>("candidates");
  const [query, setQuery] = useState("");
  const [activityMonths, setActivityMonths] = useState(12);
  // null — порядок с сервера («кого звать первым»); клик по колонке его меняет.
  const [sort, setSort] = useState<SortState | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getOrganizerVolunteerBench(slug, minRuns)
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
          setError(err instanceof Error ? err.message : "Не удалось загрузить волонтёров");
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
  }, [slug, minRuns]);

  const activityCutoff = useMemo(() => {
    if (!activityMonths) {
      return "";
    }
    const cutoff = new Date();
    cutoff.setMonth(cutoff.getMonth() - activityMonths);
    return cutoff.toISOString().slice(0, 10);
  }, [activityMonths]);

  const filteredBase = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (data?.items ?? []).filter((item) => {
      if (activityCutoff && lastSeenIso(item) < activityCutoff) {
        return false;
      }
      return !needle || (item.name ?? "").toLowerCase().includes(needle);
    });
  }, [data, query, activityCutoff]);

  const counts = useMemo(() => {
    return {
      candidates: filteredBase.filter((item) => item.is_candidate).length,
      never: filteredBase.filter((item) => item.status === "never").length,
      paused: filteredBase.filter((item) => item.status === "paused").length,
      active: filteredBase.filter((item) => item.status === "active").length,
      all: filteredBase.length,
    };
  }, [filteredBase]);

  const rows = useMemo(() => {
    let items = [...filteredBase];
    if (segment === "candidates") {
      items = items.filter((item) => item.is_candidate);
    } else if (segment !== "all") {
      items = items.filter((item) => item.status === segment);
    }
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
  }, [filteredBase, segment, sort]);

  const toggleSort = (key: SortKey) => {
    setSort((current) =>
      current && current.key === key
        ? { key, asc: !current.asc }
        : { key, asc: key === "name" },
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
        <OrganizerBreadcrumbs slug={slug} locationName={name} tool="Волонтёрская скамейка" />
        <div className="loc-header-title">
          <h1>{name ?? "Локация"} — волонтёрская скамейка</h1>
        </div>
        <p className="muted">
          Кого можно пригласить в оргкоманду. Наверху — те, кто регулярно бегает на локации и
          пока не пробовал волонтёрить.
        </p>
      </header>

      {/* Явная рамка этики раздела (требование Дмитрия 18.08.2026): список —
          справочный, а не список должников. Намеренно цветной и выше цифр,
          чтобы его прочитали раньше, чем начнут кого-то «догонять». */}
      <div className="org-notice">
        <span className="org-notice-icon" aria-hidden="true">
          🤝
        </span>
        <div className="org-notice-text">
          <strong>Волонтёрство — дело добровольное.</strong> Участник вправе бегать здесь сколько
          угодно и ни разу не волонтёрить: это нормально и не требует объяснений. Список нужен,
          чтобы оргкоманде было кого пригласить, — приглашайте бережно и спокойно принимайте
          отказ. Это справочная информация, а не повод давить на людей.
        </div>
      </div>

      <div className="map-mode-tabs org-subnav" role="tablist" aria-label="Кого показывать">
        {SEGMENTS.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={segment === item.key}
            className={segment === item.key ? "map-mode-tab active" : "map-mode-tab"}
            title={item.hint}
            onClick={() => setSegment(item.key)}
          >
            {item.label} ({formatInt(counts[item.key])})
          </button>
        ))}
      </div>

      <section className="card org-toolbar-card">
        <div className="org-toolbar-row">
          <label className="org-toolbar-label">
            Бегуны от{" "}
            <FilterSelect
              ariaLabel="Бегуны от скольких пробежек"
              value={minRuns}
              onChange={setMinRuns}
              options={MIN_RUNS_OPTIONS.map((value) => ({ value, label: `${value} пробежек здесь` }))}
            />
          </label>
          <label className="org-toolbar-label">
            Появлялись за{" "}
            <FilterSelect
              ariaLabel="Появлялись за период"
              value={activityMonths}
              onChange={setActivityMonths}
              options={ACTIVITY_OPTIONS.map((option) => ({ value: option.months, label: option.label }))}
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
              «Давно» — пропущено {data.pause_events}+ событий подряд. Событий у
              локации: {formatInt(data.events_total)}.
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

      {!loading && !error && rows.length === 0 && (
        <div className="card">
          <p className="muted">
            {segment === "candidates"
              ? "Кандидатов нет — все, кто регулярно бегает, уже в команде. Отличная новость!"
              : "Под фильтр никто не попал."}
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
                  <th>Волонтёрство</th>
                  <ColumnHeader
                    label="Пробежек"
                    hint="Пробежек на этой локации — насколько человек наш"
                    {...sortProps("runs_here")}
                  />
                  <ColumnHeader
                    label="Последняя пробежка"
                    hint="Последняя пробежка на этой локации: бегает — значит рядом, и позвать его реально"
                    {...sortProps("last_run")}
                  />
                  <ColumnHeader
                    label="Волонтёрств"
                    hint="Волонтёрств на этой локации; в скобках — всего в системе, если человек волонтёрил и в других местах"
                    {...sortProps("vols_here")}
                  />
                  <ColumnHeader
                    label="Последнее волонтёрство"
                    hint="Когда человек последний раз волонтёрил на этой локации"
                    {...sortProps("last_vol")}
                  />
                  <th>Роли</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.participant_id}>
                    <td>
                      {row.name ?? "—"}
                    </td>
                    <td>
                      <StatusBadge row={row} />
                    </td>
                    <td>{formatInt(row.runs_here)}</td>
                    <td>{row.last_run_display ?? <span className="muted">—</span>}</td>
                    <td>
                      {row.vols_here > 0 ? formatInt(row.vols_here) : <span className="muted">—</span>}
                      {row.vols_total > row.vols_here && (
                        <span className="muted"> ({formatInt(row.vols_total)} в системе)</span>
                      )}
                    </td>
                    <td>{row.last_vol_display ?? <span className="muted">никогда</span>}</td>
                    <td className="org-bench-roles">
                      <RolesCell roles={row.roles} />
                    </td>
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

export function OrganizerBenchPage({ slug }: { slug: string }) {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {() => <OrganizerBenchContent slug={slug} />}
    </RequireAuth>
  );
}
