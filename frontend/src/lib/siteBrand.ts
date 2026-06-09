/** Public-facing site name and assets (Russian branding). */
export const SITE_NAME = "Статистика парковых пробежек";

export const SITE_LOGO_SRC = "/logo.png";
export const SITE_FAVICON_SRC = "/favicon.png";

export const SITE_HOME_HREF = "/dashboard";
export const SITE_PUBLIC_HOME_HREF = "/";

/** Grafana / legacy dashboards (moved from apex run5k.run). */
export const LEGACY_SITE_DASHBOARD_PATH = "/d/ce5xtszxy4074e/glavnaja";
export const LEGACY_SITE_ORIGIN = "https://grafana.run5k.run";
export const LEGACY_SITE_HREF = `${LEGACY_SITE_ORIGIN}${LEGACY_SITE_DASHBOARD_PATH}`;
export const LEGACY_SITE_LABEL = "grafana.run5k.run";

/** Paths that belonged to Grafana when it was served from run5k.run. */
export function isLegacyGrafanaPath(path: string): boolean {
  return (
    path.startsWith("/d/")
    || path.startsWith("/dashboard/")
    || path.startsWith("/public/")
  );
}

export function legacyGrafanaHref(path: string, search = "", hash = ""): string {
  return `${LEGACY_SITE_ORIGIN}${path}${search}${hash}`;
}
