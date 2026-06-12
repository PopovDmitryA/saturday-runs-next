import { useMemo, useState } from "react";
import {
  filterDashboards,
  formatCatalogDate,
  GRAFANA_CATALOG,
  groupDashboardsByCategory,
  panelTypeLabel,
} from "../../lib/grafanaCatalog";
import { InsightsShell } from "./InsightsShell";

const NATIVE_DASHBOARD_SLUGS = new Set(["rejting-kolichestva-probezhek"]);

export function InsightsIndexPage() {
  const [query, setQuery] = useState("");
  const [categoryId, setCategoryId] = useState<string | null>(null);

  const filtered = useMemo(
    () => filterDashboards(GRAFANA_CATALOG.dashboards, query, categoryId),
    [query, categoryId],
  );
  const groups = useMemo(() => groupDashboardsByCategory(filtered), [filtered]);

  return (
    <InsightsShell activePath="/insights" title="Справочник дашбордов">
      <div className="insights-page">
        <header className="insights-header">
          <p className="about-eyebrow">Grafana → run5k</p>
          <h1 className="about-hero-title">Справочник дашбордов</h1>
          <p className="about-lead">
            Каталог дашбордов с grafana.run5k.run. Перенесённые отчёты открываются прямо на сайте; остальные
            пока в очереди на миграцию.
          </p>
          <p className="insights-meta muted">
            Каталог от {formatCatalogDate(GRAFANA_CATALOG.generatedAt)} ·{" "}
            <a href={GRAFANA_CATALOG.grafanaOrigin} target="_blank" rel="noreferrer">
              grafana.run5k.run
            </a>
          </p>
        </header>

        <div className="insights-toolbar">
          <label className="insights-search">
            <span className="visually-hidden">Поиск по дашбордам</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Поиск по названию, описанию, панелям…"
            />
          </label>
          <div className="insights-filters" role="tablist" aria-label="Категории">
            <button
              type="button"
              className={`insights-filter-chip${categoryId === null ? " active" : ""}`}
              onClick={() => setCategoryId(null)}
            >
              Все ({GRAFANA_CATALOG.dashboards.length})
            </button>
            {GRAFANA_CATALOG.categories.map((category) => {
              const count = GRAFANA_CATALOG.dashboards.filter(
                (item) => item.category === category.id,
              ).length;
              if (count === 0) {
                return null;
              }
              return (
                <button
                  key={category.id}
                  type="button"
                  className={`insights-filter-chip${categoryId === category.id ? " active" : ""}`}
                  onClick={() => setCategoryId(category.id)}
                >
                  {category.label} ({count})
                </button>
              );
            })}
          </div>
        </div>

        {filtered.length === 0 ? (
          <p className="muted">Ничего не найдено. Попробуйте другой запрос или категорию.</p>
        ) : (
          groups.map(({ category, items }) => (
            <section key={category.id} className="insights-category">
              <h2 className="insights-category-title">{category.label}</h2>
              <div className="insights-grid">
                {items.map((dashboard) => {
                  const isNative = NATIVE_DASHBOARD_SLUGS.has(dashboard.slug);
                  return (
                  <article key={dashboard.uid} className="insights-card">
                    <div className="insights-card-head">
                      <h3 className="insights-card-title">
                        <a href={`/insights/${dashboard.slug}`}>{dashboard.title}</a>
                      </h3>
                      <span className="insights-badge">{isNative ? "На сайте" : `${dashboard.panelCount} пан.`}</span>
                    </div>
                    {dashboard.description ? (
                      <p className="insights-card-desc">{dashboard.description}</p>
                    ) : (
                      <p className="insights-card-desc muted">
                        {dashboard.panels.slice(0, 3).map((panel) => panel.title).join(" · ")}
                      </p>
                    )}
                    <div className="insights-card-tags">
                      {dashboard.panelTypes.map((type) => (
                        <span key={type} className="insights-tag">
                          {panelTypeLabel(type)}
                        </span>
                      ))}
                    </div>
                    <div className="insights-card-actions">
                      <a className="btn btn-sm" href={`/insights/${dashboard.slug}`}>
                        {isNative ? "Открыть" : "Обзор"}
                      </a>
                      {!isNative ? (
                        <a
                          className="btn btn-ghost btn-sm"
                          href={dashboard.grafanaUrl}
                          target="_blank"
                          rel="noreferrer"
                        >
                          В Grafana
                        </a>
                      ) : null}
                    </div>
                  </article>
                  );
                })}
              </div>
            </section>
          ))
        )}
      </div>
    </InsightsShell>
  );
}
