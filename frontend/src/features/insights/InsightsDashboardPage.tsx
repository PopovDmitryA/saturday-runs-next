import {
  formatCatalogDate,
  getDashboardBySlug,
  panelTypeLabel,
  type GrafanaPanelSummary,
} from "../../lib/grafanaCatalog";
import { InsightsShell } from "./InsightsShell";
import { NotFoundPage } from "../NotFoundPage";

type InsightsDashboardPageProps = {
  slug: string;
};

function groupPanelsByType(panels: GrafanaPanelSummary[]): Array<{ type: string; items: GrafanaPanelSummary[] }> {
  const byType = new Map<string, GrafanaPanelSummary[]>();
  for (const panel of panels) {
    const bucket = byType.get(panel.type) ?? [];
    bucket.push(panel);
    byType.set(panel.type, bucket);
  }
  return [...byType.entries()]
    .sort(([left], [right]) => left.localeCompare(right, "ru"))
    .map(([type, items]) => ({ type, items }));
}

export function InsightsDashboardPage({ slug }: InsightsDashboardPageProps) {
  const dashboard = getDashboardBySlug(slug);
  if (!dashboard) {
    return <NotFoundPage />;
  }

  const panelGroups = groupPanelsByType(dashboard.panels);

  return (
    <InsightsShell activePath="/insights" title={dashboard.title}>
      <div className="insights-page insights-detail">
        <nav className="insights-breadcrumb">
          <a href="/insights">Справочник</a>
          <span aria-hidden> / </span>
          <span>{dashboard.title}</span>
        </nav>

        <header className="insights-header">
          <div className="insights-detail-head">
            <span className="insights-badge insights-badge-category">{dashboard.categoryLabel}</span>
            <h1 className="about-hero-title">{dashboard.title}</h1>
          </div>
          {dashboard.description ? (
            <p className="about-lead">{dashboard.description}</p>
          ) : null}
          <div className="insights-detail-meta muted">
            <span>{dashboard.panelCount} панелей</span>
            {dashboard.updatedAt ? (
              <>
                <span aria-hidden> · </span>
                <span>обновлён {formatCatalogDate(dashboard.updatedAt)}</span>
              </>
            ) : null}
          </div>
          <div className="insights-detail-actions">
            <a className="btn primary" href={dashboard.grafanaUrl} target="_blank" rel="noreferrer">
              Открыть в Grafana
            </a>
            <a className="btn btn-ghost" href="/insights">
              К каталогу
            </a>
          </div>
          <p className="insights-note">
            Grafana не разрешает встраивание (X-Frame-Options). Интерактивный просмотр — по ссылке
            выше; здесь — структура и описание панелей для навигации и будущего переноса на сайт.
          </p>
        </header>

        {dashboard.variables.length > 0 ? (
          <section className="insights-section">
            <h2 className="insights-section-title">Фильтры дашборда</h2>
            <ul className="insights-variable-list">
              {dashboard.variables.map((variable) => (
                <li key={variable.name} className="insights-variable-item">
                  <strong>{variable.label}</strong>
                  <span className="muted">
                    {variable.name}
                    {variable.type ? ` · ${variable.type}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        <section className="insights-section">
          <h2 className="insights-section-title">Панели</h2>
          {panelGroups.map(({ type, items }) => (
            <div key={type} className="insights-panel-group">
              <h3 className="insights-panel-group-title">
                {panelTypeLabel(type)} <span className="muted">({items.length})</span>
              </h3>
              <ul className="insights-panel-list">
                {items.map((panel, index) => (
                  <li key={`${panel.title}-${index}`} className="insights-panel-item">
                    <div className="insights-panel-item-head">
                      <span className="insights-panel-item-title">{panel.title}</span>
                    </div>
                    {panel.description ? (
                      <p className="insights-panel-item-desc">{panel.description}</p>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </section>
      </div>
    </InsightsShell>
  );
}
