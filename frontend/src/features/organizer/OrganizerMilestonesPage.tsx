import { useEffect, useMemo, useState } from "react";
import { RequireAuth } from "../../components/RequireAuth";
import { ColumnHeader } from "../../components/activityTable/ColumnHeader";
import { FilterSelect } from "../../components/filters/FilterPanel";
import {
  ApiError,
  getOrganizerMilestones,
  type OrganizerMilestoneItem,
  type OrganizerMilestonesResponse,
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

const KIND_FILTERS: { key: string; label: string }[] = [
  { key: "all", label: "Все" },
  { key: "runs_here", label: "Пробежки здесь" },
  { key: "runs_platform", label: "Пробежки в системе" },
  { key: "vols_here", label: "Волонтёрства здесь" },
  { key: "vols_platform", label: "Волонтёрства в системе" },
];

type SortKey = "name" | "kind" | "current" | "milestone" | "remaining" | "last_seen";
type SortState = { key: SortKey; asc: boolean };

// Строка таблицы: один человек может идти к одному юбилею сразу «здесь» и
// «в системе» (счёт совпадает) — такие пары схлопнуты в одну строку с двумя
// лейблами (правка Дмитрия 24.08.2026). Разные уровни — разные строки.
type MergedMilestoneRow = OrganizerMilestoneItem & {
  kinds: { kind: string; kind_label: string }[];
};

function mergeMilestoneRows(items: OrganizerMilestoneItem[]): MergedMilestoneRow[] {
  const byKey = new Map<string, MergedMilestoneRow>();
  for (const item of items) {
    // Пробежки и волонтёрства не смешиваем; внутри активности склеиваем
    // «здесь» + «в системе» при совпадении текущего счёта и юбилея.
    const activity = item.kind.startsWith("runs") ? "runs" : "vols";
    const key = `${item.participant_id}|${activity}|${item.current}|${item.milestone}`;
    const existing = byKey.get(key);
    if (existing) {
      existing.kinds.push({ kind: item.kind, kind_label: item.kind_label });
    } else {
      byKey.set(key, { ...item, kinds: [{ kind: item.kind, kind_label: item.kind_label }] });
    }
  }
  return [...byKey.values()];
}

function sortValue(row: MergedMilestoneRow, key: SortKey): number | string {
  switch (key) {
    case "name":
      return row.name ?? "";
    case "kind":
      return row.kinds[0]?.kind_label ?? "";
    case "current":
      return row.current;
    case "milestone":
      return row.milestone;
    case "remaining":
      return row.remaining;
    case "last_seen":
      return row.last_seen ?? "";
  }
}

function OrganizerMilestonesContent({ slug }: { slug: string }) {
  const attachFloatingHead = useFloatingTableHead();
  const [data, setData] = useState<OrganizerMilestonesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [kindFilter, setKindFilter] = useState("all");
  const [query, setQuery] = useState("");
  // Длина пропуска, после которой человек выпадает из календаря. Тринадцать
  // недель — прежнее зашитое поведение (90 дней).
  const [absenceWeeks, setAbsenceWeeks] = useState(13);
  // По умолчанию — ближайшие юбилеи; клик по «Юбилей» покажет самые крупные.
  const [sort, setSort] = useState<SortState>({ key: "remaining", asc: true });

  useEffect(() => {
    let cancelled = false;
    getOrganizerMilestones(slug, absenceWeeks)
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
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
          setError(err instanceof Error ? err.message : "Не удалось загрузить календарь");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug, absenceWeeks]);

  const rows = useMemo(() => {
    const merged = mergeMilestoneRows(data?.items ?? []);
    const needle = query.trim().toLowerCase();
    const filtered = merged.filter((item) => {
      if (kindFilter !== "all" && !item.kinds.some((entry) => entry.kind === kindFilter)) {
        return false;
      }
      return !needle || (item.name ?? "").toLowerCase().includes(needle);
    });
    filtered.sort((a, b) => {
      const left = sortValue(a, sort.key);
      const right = sortValue(b, sort.key);
      if (left === right) {
        // При равенстве — более крупный юбилей выше: он важнее для оргкоманды.
        if (a.milestone !== b.milestone) {
          return b.milestone - a.milestone;
        }
        return (a.name ?? "") < (b.name ?? "") ? -1 : 1;
      }
      const compare = left < right ? -1 : 1;
      return sort.asc ? compare : -compare;
    });
    return filtered;
  }, [data, kindFilter, query, sort]);

  const toggleSort = (key: SortKey) => {
    setSort((current) =>
      current.key === key
        ? { key, asc: !current.asc }
        : // Числовые колонки логичнее открывать с крупных значений,
          // «Осталось» — с ближайших юбилеев.
          { key, asc: key === "name" || key === "kind" || key === "remaining" },
    );
  };

  const sortProps = (key: SortKey) => ({
    filterable: false,
    sortActive: sort.key === key,
    sortAsc: sort.asc,
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
        <OrganizerBreadcrumbs slug={slug} locationName={name} tool="Календарь юбилеев" />
        <div className="loc-header-title">
          <h1>{name ?? "Локация"} — календарь юбилеев</h1>
        </div>
        <p className="muted">
          Кто из недавних участников локации подходит к юбилею (10, дальше кратные 25) — чтобы
          успеть подготовить поздравление заранее, а не узнать из свода задним числом.
        </p>
      </header>

      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!error && data === null && <p className="muted">Загрузка…</p>}

      {!error && data !== null && (
        <>
          <div className="map-mode-tabs org-subnav" role="tablist" aria-label="Тип юбилея">
            {KIND_FILTERS.map((filter) => (
              <button
                key={filter.key}
                type="button"
                role="tab"
                aria-selected={kindFilter === filter.key}
                className={kindFilter === filter.key ? "map-mode-tab active" : "map-mode-tab"}
                onClick={() => setKindFilter(filter.key)}
              >
                {filter.label}
              </button>
            ))}
            <span className="muted org-subnav-note">
              юбилеи на ближайшие {pluralizeRu(data.horizon, ["участие", "участия", "участий"])}
            </span>
          </div>

          <div className="org-toolbar-row">
            <label className="org-toolbar-label">
              Пропуск не больше{" "}
              <FilterSelect
                ariaLabel="Исключать при пропуске дольше"
                value={absenceWeeks}
                onChange={setAbsenceWeeks}
                options={ABSENCE_WEEK_OPTIONS.map((value) => ({
                  value,
                  // pluralizeRu САМА возвращает число вместе с формой
                  // («13 недель»). Число перед ней давало «13 13 недель» на
                  // экране (Дмитрий 04.09.2026). Формы тоже были сдвинуты:
                  // именительный — «неделя», а не «недели».
                  label: pluralizeRu(value, ["неделя", "недели", "недель"]),
                }))}
              />
            </label>
            <span className="muted">
              Кто не приходил дольше выбранного срока, в календарь не попадает.
            </span>
          </div>

          <p className="org-index-search">
            <input
              className="input"
              type="search"
              placeholder="Поиск по имени или фамилии…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </p>

          {rows.length === 0 && (
            <div className="card">
              <p className="muted">
                Юбилеев на горизонте нет — либо все недавно отпраздновали, либо смените фильтр.
              </p>
            </div>
          )}

          {rows.length > 0 && (
            <section className="card org-table-card">
              <TableWrap stickyFirstCol innerRef={attachFloatingHead}>
                <table className="data-table org-svod-table">
                  <thead>
                    <tr>
                      <ColumnHeader label="Имя" {...sortProps("name")} />
                      <ColumnHeader label="Что за юбилей" {...sortProps("kind")} />
                      <ColumnHeader
                        label="Сейчас"
                        hint="Текущий счёт участий"
                        {...sortProps("current")}
                      />
                      <ColumnHeader
                        label="Юбилей"
                        hint="Юбилейное число — сортировка покажет самые крупные"
                        {...sortProps("milestone")}
                      />
                      <ColumnHeader
                        label="Осталось"
                        hint="Сколько участий до юбилея"
                        {...sortProps("remaining")}
                      />
                      <ColumnHeader
                        label="Последний визит"
                        hint="Когда последний раз был на локации"
                        {...sortProps("last_seen")}
                      />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={`${row.participant_id}-${row.kinds.map((k) => k.kind).join("+")}-${row.milestone}`}>
                        <td>
                          {row.name ?? "—"}
                        </td>
                        <td>
                          <span className="org-badges">
                            {row.kinds.map((entry) => (
                              <span
                                key={entry.kind}
                                className={`org-badge ${
                                  entry.kind.startsWith("runs") ? "org-kind-run" : "org-kind-vol"
                                }`}
                              >
                                {entry.kind_label}
                              </span>
                            ))}
                          </span>
                        </td>
                        <td>{formatInt(row.current)}</td>
                        <td>
                          <span
                            className={`org-badge org-badge-milestone ${
                              row.kind.startsWith("runs") ? "org-kind-run" : "org-kind-vol"
                            }`}
                          >
                            {row.milestone}
                          </span>
                        </td>
                        <td>
                          {row.remaining === 1 ? (
                            <strong>следующая!</strong>
                          ) : (
                            formatInt(row.remaining)
                          )}
                        </td>
                        <td>{row.last_seen_display ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            </section>
          )}
        </>
      )}
    </PortalSectionShell>
  );
}

// Та же лестница, что на странице пост-отчёта: пороги должны совпадать.
const ABSENCE_WEEK_OPTIONS = [2, 4, 8, 13, 20, 26, 39, 52, 78, 100];

export function OrganizerMilestonesPage({ slug }: { slug: string }) {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {() => <OrganizerMilestonesContent slug={slug} />}
    </RequireAuth>
  );
}
