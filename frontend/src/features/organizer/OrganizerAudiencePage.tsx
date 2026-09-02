import { useEffect, useState } from "react";
import { FilterSelect } from "../../components/filters/FilterPanel";
import { RequireAuth } from "../../components/RequireAuth";
import {
  ApiError,
  getOrganizerAudience,
  getOrganizerBenchmark,
  type OrganizerAudienceResponse,
  type OrganizerBenchmarkResponse,
} from "../../lib/api";
import { formatInt } from "../../lib/format";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { locationHintFor } from "../../lib/locationHint";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { OrganizerBreadcrumbs } from "./OrganizerBreadcrumbs";
import { OrganizerDenied } from "./OrganizerDenied";
import "./organizer.css";

const SCOPES = [
  { key: "city", label: "Город" },
  { key: "region", label: "Регион" },
  { key: "nearest", label: "3 ближайшие" },
  { key: "network", label: "Вся система" },
];

// Период сравнения. 0 — текущий календарный год; «всё время» — 10 лет:
// старше данных в системе нет.
const PERIOD_OPTIONS = [
  { months: 0, label: "текущий год" },
  { months: 6, label: "полгода" },
  { months: 12, label: "12 месяцев" },
  { months: 120, label: "всё время" },
];

/** Портрет участника + сравнение с соседями — одна страница «Кто к нам ходит». */
function OrganizerAudienceContent({ slug }: { slug: string }) {
  const [audience, setAudience] = useState<OrganizerAudienceResponse | null>(null);
  const [benchmark, setBenchmark] = useState<OrganizerBenchmarkResponse | null>(null);
  // Дефолт — вся система: она есть у любой локации; город/регион/ближайшие
  // включаются вкладками, когда там есть с кем сравнивать (scope_sizes).
  const [scope, setScope] = useState("network");
  const [months, setMonths] = useState(12);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getOrganizerAudience(slug)
      .then((payload) => {
        if (!cancelled) {
          setAudience(payload);
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
          setError(err instanceof Error ? err.message : "Не удалось загрузить портрет участника");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  useEffect(() => {
    let cancelled = false;
    setBenchmark(null);
    getOrganizerBenchmark(slug, scope, months)
      .then((payload) => {
        if (!cancelled) {
          setBenchmark(payload);
        }
      })
      .catch(() => {
        // Бенчмарк — второстепенный блок: молчаливо остаётся пустым.
      });
    return () => {
      cancelled = true;
    };
  }, [slug, scope, months]);

  const name = audience?.location.name ?? locationHintFor(slug)?.name ?? null;
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

  const maxAgeShare = Math.max(1, ...(audience?.age_groups ?? []).map((g) => g.share_pct));

  return (
    <PortalSectionShell sidebar={sidebar}>
      <header className="loc-header">
        <OrganizerBreadcrumbs slug={slug} locationName={name} tool="Портрет участника" />
        <div className="loc-header-title">
          <h1>{name ?? "Локация"} — портрет участника</h1>
        </div>
        <p className="muted">
          Кто к нам ходит: возраст, пол и клубы за последние 12 месяцев — и как локация выглядит
          на фоне соседей.
        </p>
      </header>

      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!error && audience === null && <p className="muted">Загрузка…</p>}

      {!error && audience !== null && audience.finishes_total === 0 && (
        <div className="card">
          <p className="muted">За последний год финишей на локации не было.</p>
        </div>
      )}

      {!error && audience !== null && audience.finishes_total > 0 && (
        <>
          <section className="card org-toolbar-card">
            <div className="org-toolbar-row">
              <span className="muted">
                За 12 месяцев: {formatInt(audience.finishes_total)} финишей от{" "}
                {formatInt(audience.people_total)} разных участников
                {audience.genders.map((gender) => (
                  <span key={gender.label}>
                    {" "}
                    · {gender.label.toLowerCase()}: {gender.share_pct}%
                  </span>
                ))}
              </span>
            </div>
          </section>

          <div className="org-two-col">
            <section className="card org-table-card">
              <header className="org-table-head">
                <h2 className="section-title">
                  <span className="org-table-emoji" aria-hidden="true">
                    🎂
                  </span>
                  Возрастные группы
                </h2>
                <span className="muted org-table-count">доля финишей за год</span>
              </header>
              <div className="org-age-bars">
                {audience.age_groups.map((group) => (
                  <div key={group.group} className="org-age-row" title={`${group.group}: ${formatInt(group.finishes)} финишей (${group.share_pct}%)`}>
                    <span className="org-age-label">{group.group}</span>
                    <div className="org-bar-row">
                      <div
                        className="org-bar"
                        style={{ width: `${(group.share_pct / maxAgeShare) * 100}%` }}
                      />
                      <span>{group.share_pct}%</span>
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <section className="card org-table-card">
              <header className="org-table-head">
                <h2 className="section-title">
                  <span className="org-table-emoji" aria-hidden="true">
                    👥
                  </span>
                  Клубы на локации
                </h2>
                <span className="muted org-table-count">по клубу в профиле участника</span>
              </header>
              {audience.clubs.length === 0 ? (
                <p className="muted">Участники с клубами в профиле пока не отметились.</p>
              ) : (
                <TableWrap>
                  <table className="data-table org-svod-table">
                    <thead>
                      <tr>
                        <th>Клуб</th>
                        <th>Людей</th>
                        <th>Финишей</th>
                      </tr>
                    </thead>
                    <tbody>
                      {audience.clubs.map((club) => (
                        <tr key={club.club}>
                          <td>{club.club}</td>
                          <td>{formatInt(club.people)}</td>
                          <td>{formatInt(club.finishes)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableWrap>
              )}
            </section>
          </div>

          <section className="card org-table-card">
            <header className="org-table-head">
              <h2 className="section-title">
                <span className="org-table-emoji" aria-hidden="true">
                  ⚖️
                </span>
                Мы и соседи
              </h2>
            </header>
            {/* Фильтры — отдельной строкой под заголовком: тип сравнения + период
                вместе, размером друг под друга (правка Дмитрия 24.08.2026). */}
            <div className="org-benchmark-controls">
              <span className="muted org-benchmark-controls-label">Сравниваем с:</span>
              <div className="map-mode-tabs org-benchmark-tabs" role="tablist">
                {SCOPES.filter((item) => {
                  // Вкладка есть, только когда в выборке есть с кем сравнивать
                  // (наша локация + хотя бы одна соседняя). Пока размеры не
                  // приехали — показываем всё, чтобы табы не прыгали.
                  const sizes = benchmark?.scope_sizes;
                  if (!sizes || item.key === "network") {
                    return true;
                  }
                  return (sizes[item.key] ?? 0) >= 2;
                }).map((item) => (
                  <button
                    key={item.key}
                    type="button"
                    role="tab"
                    aria-selected={scope === item.key}
                    className={scope === item.key ? "map-mode-tab active" : "map-mode-tab"}
                    onClick={() => setScope(item.key)}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <span className="muted org-benchmark-controls-label">за</span>
              <FilterSelect
                ariaLabel="Период сравнения"
                title="За какой период сравниваем локации"
                value={months}
                onChange={setMonths}
                options={PERIOD_OPTIONS.map((option) => ({ value: option.months, label: option.label }))}
              />
            </div>
            {benchmark === null && <p className="muted">Считаем сравнение…</p>}
            {benchmark !== null && benchmark.metrics.length === 0 && (
              <p className="muted">Пока не с кем сравнивать.</p>
            )}
            {benchmark !== null && benchmark.metrics.length > 0 && (
              <>
                <p className="muted org-benchmark-note">
                  {benchmark.scope === "nearest" ? (
                    <>
                      Сравнение с тремя ближайшими:{" "}
                      {benchmark.peers
                        .filter((peer) => !peer.is_ours)
                        .map((peer) => peer.name)
                        .join(", ")}
                      .
                    </>
                  ) : (
                    <>
                      Сравнение: {benchmark.scope_label}, локаций в выборке —{" "}
                      {formatInt(benchmark.peers_total)} (с 5+ стартами за период).
                    </>
                  )}
                </p>
                <TableWrap>
                  <table className="data-table org-svod-table">
                    <thead>
                      <tr>
                        <th>Метрика</th>
                        <th>Мы</th>
                        <th title="Середина выборки: половина локаций выше, половина ниже">
                          Медиана
                        </th>
                        <th>Лучшая</th>
                        <th>Наше место</th>
                      </tr>
                    </thead>
                    <tbody>
                      {benchmark.metrics.map((metric) => (
                        <tr key={metric.key}>
                          <td>{metric.label}</td>
                          <td>
                            <strong>{metric.our_value}</strong>
                            {metric.delta_vs_median_pct != null && (
                              <span
                                className={
                                  metric.delta_vs_median_pct >= 0 ? "org-delta-good" : "org-delta-bad"
                                }
                              >
                                {" "}
                                ({metric.delta_vs_median_pct > 0 ? "+" : ""}
                                {metric.delta_vs_median_pct}%)
                              </span>
                            )}
                          </td>
                          <td>{metric.median ?? "—"}</td>
                          <td>{metric.best ?? "—"}</td>
                          <td>
                            {metric.rank != null ? `${metric.rank} из ${metric.peers}` : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableWrap>
              </>
            )}
          </section>
        </>
      )}
    </PortalSectionShell>
  );
}

export function OrganizerAudiencePage({ slug }: { slug: string }) {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {() => <OrganizerAudienceContent slug={slug} />}
    </RequireAuth>
  );
}
