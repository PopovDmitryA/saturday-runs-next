import type { MapLocationPoint } from "../../lib/api";

export type MapPlatformCode = "five_verst" | "s95" | "runpark";

export type PlatformFilters = Record<MapPlatformCode, boolean>;

export const DEFAULT_PLATFORM_FILTERS: PlatformFilters = {
  five_verst: true,
  s95: true,
  runpark: true,
};

const MAP_PLATFORM_CODES: MapPlatformCode[] = ["five_verst", "s95", "runpark"];

export function coordKey(point: MapLocationPoint): string {
  return `${point.latitude.toFixed(5)},${point.longitude.toFixed(5)}`;
}

export function pointPlatformCode(point: MapLocationPoint): MapPlatformCode | null {
  const code = point.active_platform ?? point.platform_codes[0];
  if (code === "five_verst" || code === "s95" || code === "runpark") {
    return code;
  }
  return null;
}

export function filterPointsByPlatform(
  points: MapLocationPoint[],
  filters: PlatformFilters,
  options?: { onlyMapPlatforms?: boolean },
): MapLocationPoint[] {
  const onlyMapPlatforms = options?.onlyMapPlatforms ?? false;
  return points.filter((point) => {
    const mapCodes = point.platform_codes.filter((code): code is MapPlatformCode =>
      MAP_PLATFORM_CODES.includes(code as MapPlatformCode),
    );

    if (onlyMapPlatforms) {
      if (mapCodes.length === 0) {
        return false;
      }
      return mapCodes.some((code) => filters[code]);
    }

    if (mapCodes.length === 0) {
      return true;
    }
    return mapCodes.some((code) => filters[code]);
  });
}

export function buildVisitedByIdentity(
  points: MapLocationPoint[],
): Map<string, MapLocationPoint> {
  const map = new Map<string, MapLocationPoint>();
  for (const point of points) {
    if (point.catalog_identity_key) {
      map.set(point.catalog_identity_key, point);
    }
  }
  return map;
}

export function buildVisitedByCoord(
  points: MapLocationPoint[],
): Map<string, MapLocationPoint> {
  const map = new Map<string, MapLocationPoint>();
  for (const point of points) {
    map.set(coordKey(point), point);
  }
  return map;
}

export function hasActivePlatformFilter(filters: PlatformFilters): boolean {
  return filters.five_verst || filters.s95 || filters.runpark;
}

export type ActivityFilter = "runs" | "volunteering";

export function filterPointsByActivity(
  points: MapLocationPoint[],
  activity: ActivityFilter,
): MapLocationPoint[] {
  if (activity === "runs") {
    return points.filter((p) => (p.run_dates?.length ?? 0) > 0);
  }
  return points.filter((p) => (p.volunteer_dates?.length ?? 0) > 0);
}

export function excludeVisitedPoints(
  points: MapLocationPoint[],
  visitedByIdentity: Map<string, MapLocationPoint>,
): MapLocationPoint[] {
  return points.filter(
    (point) =>
      point.catalog_identity_key == null || !visitedByIdentity.has(point.catalog_identity_key),
  );
}

export function countVisitedMatchingCatalogPoints(
  catalogPoints: MapLocationPoint[],
  visitedByIdentity: Map<string, MapLocationPoint>,
): number {
  return catalogPoints.filter(
    (point) =>
      point.catalog_identity_key != null &&
      visitedByIdentity.has(point.catalog_identity_key),
  ).length;
}
