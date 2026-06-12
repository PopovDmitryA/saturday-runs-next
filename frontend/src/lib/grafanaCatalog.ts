import catalogJson from "../data/grafana/catalog.json";

export type GrafanaPanelSummary = {
  title: string;
  type: string;
  description: string;
};

export type GrafanaVariableSummary = {
  name: string;
  label: string;
  type: string;
};

export type GrafanaDashboardEntry = {
  uid: string;
  slug: string;
  title: string;
  description: string;
  category: string;
  categoryLabel: string;
  grafanaUrl: string;
  updatedAt: string;
  panelCount: number;
  panelTypes: string[];
  panels: GrafanaPanelSummary[];
  variables: GrafanaVariableSummary[];
};

export type GrafanaCategory = {
  id: string;
  label: string;
};

export type GrafanaCatalog = {
  generatedAt: string;
  grafanaOrigin: string;
  categories: GrafanaCategory[];
  dashboards: GrafanaDashboardEntry[];
};

export const GRAFANA_CATALOG = catalogJson as GrafanaCatalog;

export const PANEL_TYPE_LABELS: Record<string, string> = {
  table: "Таблица",
  stat: "Показатель",
  text: "Текст",
  geomap: "Карта",
  timeseries: "График",
  barchart: "Гистограмма",
  piechart: "Диаграмма",
  gauge: "Индикатор",
  heatmap: "Тепловая карта",
  row: "Секция",
};

export function panelTypeLabel(type: string): string {
  return PANEL_TYPE_LABELS[type] ?? type;
}

export function getDashboardBySlug(slug: string): GrafanaDashboardEntry | undefined {
  return GRAFANA_CATALOG.dashboards.find((item) => item.slug === slug);
}

export function groupDashboardsByCategory(
  dashboards: GrafanaDashboardEntry[],
): Array<{ category: GrafanaCategory; items: GrafanaDashboardEntry[] }> {
  const byId = new Map<string, GrafanaDashboardEntry[]>();
  for (const dashboard of dashboards) {
    const bucket = byId.get(dashboard.category) ?? [];
    bucket.push(dashboard);
    byId.set(dashboard.category, bucket);
  }
  return GRAFANA_CATALOG.categories
    .map((category) => ({
      category,
      items: byId.get(category.id) ?? [],
    }))
    .filter((group) => group.items.length > 0);
}

export function filterDashboards(
  dashboards: GrafanaDashboardEntry[],
  query: string,
  categoryId: string | null,
): GrafanaDashboardEntry[] {
  const normalized = query.trim().toLowerCase();
  return dashboards.filter((dashboard) => {
    if (categoryId && dashboard.category !== categoryId) {
      return false;
    }
    if (!normalized) {
      return true;
    }
    const haystack = [
      dashboard.title,
      dashboard.description,
      dashboard.categoryLabel,
      ...dashboard.panelTypes.map(panelTypeLabel),
      ...dashboard.panels.map((panel) => panel.title),
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(normalized);
  });
}

export function formatCatalogDate(iso: string): string {
  if (!iso) {
    return "";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return date.toLocaleString("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}
