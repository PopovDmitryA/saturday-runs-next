import type { CatalogLocationTableRow } from "../../lib/api";
import { platformCodeLabel } from "../../lib/format";
import { resolveVisit, type MapPlatformCode, type PlatformFilters } from "./mapFilters";

export type CatalogTableSortKey =
  | "name"
  | "city"
  | "region"
  | "platform"
  | "status"
  | "visited"
  | "first_visit";

export type VisitedFilter = "all" | "visited" | "not_visited";

export type LocationRegistryStatus = "active" | "paused" | "cancelled";

export type CatalogTableFilters = {
  cities: Set<string>;
  regions: Set<string>;
  locations: Set<string>;
  platforms: Set<string>;
  visited: VisitedFilter;
  excludePaused: boolean;
  locationStatuses: Set<LocationRegistryStatus>;
};

export const LOCATION_STATUS_OPTIONS: Array<{ value: LocationRegistryStatus; label: string }> = [
  { value: "active", label: "Активна" },
  { value: "paused", label: "Не действует" },
  { value: "cancelled", label: "Отменена" },
];

export const EMPTY_CATALOG_TABLE_FILTERS: CatalogTableFilters = {
  cities: new Set(),
  regions: new Set(),
  locations: new Set(),
  platforms: new Set(),
  visited: "all",
  excludePaused: false,
  locationStatuses: new Set(),
};

const PLATFORM_ORDER: MapPlatformCode[] = ["five_verst", "s95", "runpark"];

export function platformSortIndex(code: string): number {
  const index = PLATFORM_ORDER.indexOf(code as MapPlatformCode);
  return index === -1 ? PLATFORM_ORDER.length : index;
}

export function catalogRowPlatformCode(row: CatalogLocationTableRow): MapPlatformCode | null {
  if (row.platform_code === "five_verst" || row.platform_code === "s95" || row.platform_code === "runpark") {
    return row.platform_code;
  }
  return null;
}

export function catalogRowStatus(row: CatalogLocationTableRow): LocationRegistryStatus {
  if (row.is_cancelled) {
    return "cancelled";
  }
  if (row.is_paused) {
    return "paused";
  }
  return "active";
}

export function filterCatalogTableRows(
  rows: CatalogLocationTableRow[],
  {
    platformFilters,
    mapMode,
    tableFilters,
  }: {
    platformFilters: PlatformFilters;
    mapMode: "all" | "unvisited" | "visited";
    tableFilters: CatalogTableFilters;
  },
): CatalogLocationTableRow[] {
  return rows.filter((row) => {
    const platformCode = catalogRowPlatformCode(row);
    if (platformCode === null || !platformFilters[platformCode]) {
      return false;
    }
    // «Посещал» зависит от фильтра систем: сузили до 5 вёрст — визит
    // parkrun-эпохи не в счёт, локация снова непосещённая.
    const visited = resolveVisit(row.visits_by_platform, platformFilters) !== null;
    if (tableFilters.platforms.size > 0 && !tableFilters.platforms.has(row.platform_code)) {
      return false;
    }
    if (tableFilters.locations.size > 0 && !tableFilters.locations.has(row.row_key)) {
      return false;
    }
    if (tableFilters.excludePaused && (row.is_paused || row.is_cancelled)) {
      return false;
    }
    if (
      tableFilters.locationStatuses.size > 0 &&
      !tableFilters.locationStatuses.has(catalogRowStatus(row))
    ) {
      return false;
    }
    if (mapMode === "visited" && !visited) {
      return false;
    }
    if (mapMode === "unvisited" && visited) {
      return false;
    }
    if (tableFilters.visited === "visited" && !visited) {
      return false;
    }
    if (tableFilters.visited === "not_visited" && visited) {
      return false;
    }
    if (tableFilters.cities.size > 0) {
      const city = row.city ?? "";
      if (!tableFilters.cities.has(city)) {
        return false;
      }
    }
    if (tableFilters.regions.size > 0) {
      const region = row.region ?? "";
      if (!tableFilters.regions.has(region)) {
        return false;
      }
    }
    return true;
  });
}

export function sortCatalogTableRows(
  rows: CatalogLocationTableRow[],
  sortKey: CatalogTableSortKey,
  sortAsc: boolean,
): CatalogLocationTableRow[] {
  const sorted = [...rows];
  sorted.sort((left, right) => {
    let result = 0;
    switch (sortKey) {
      case "city":
        result = (left.city ?? "").localeCompare(right.city ?? "", "ru");
        break;
      case "region":
        result = (left.region ?? "").localeCompare(right.region ?? "", "ru");
        break;
      case "platform": {
        const leftIndex = platformSortIndex(left.platform_code);
        const rightIndex = platformSortIndex(right.platform_code);
        result = leftIndex - rightIndex || left.platform_code.localeCompare(right.platform_code);
        break;
      }
      case "status": {
        const statusOrder: Record<LocationRegistryStatus, number> = {
          active: 0,
          paused: 1,
          cancelled: 2,
        };
        result = statusOrder[catalogRowStatus(left)] - statusOrder[catalogRowStatus(right)];
        break;
      }
      case "visited":
        result = Number(left.visited) - Number(right.visited);
        break;
      case "first_visit":
        result = (left.first_visit_date ?? "").localeCompare(right.first_visit_date ?? "");
        break;
      case "name":
      default:
        result = left.name.localeCompare(right.name, "ru");
        break;
    }
    if (result === 0) {
      result = left.name.localeCompare(right.name, "ru");
    }
    return sortAsc ? result : -result;
  });
  return sorted;
}

export function defaultCatalogTableSortAsc(column: CatalogTableSortKey): boolean {
  if (column === "visited" || column === "first_visit") {
    return false;
  }
  return true;
}

export function toggleCatalogTableSort(
  currentKey: CatalogTableSortKey,
  currentAsc: boolean,
  column: CatalogTableSortKey,
): { sortKey: CatalogTableSortKey; sortAsc: boolean } {
  if (currentKey === column) {
    return { sortKey: column, sortAsc: !currentAsc };
  }
  return { sortKey: column, sortAsc: defaultCatalogTableSortAsc(column) };
}

export function collectUniqueColumnValues(
  rows: CatalogLocationTableRow[],
  field: "city" | "region",
): string[] {
  const values = new Set<string>();
  for (const row of rows) {
    const value = field === "city" ? row.city : row.region;
    values.add(value ?? "");
  }
  return [...values].sort((left, right) => left.localeCompare(right, "ru"));
}

export function collectUniqueLocationOptions(
  rows: CatalogLocationTableRow[],
): Array<{ value: string; label: string }> {
  const nameCounts = new Map<string, number>();
  for (const row of rows) {
    nameCounts.set(row.name, (nameCounts.get(row.name) ?? 0) + 1);
  }

  const options = rows.map((row) => {
    const duplicateName = (nameCounts.get(row.name) ?? 0) > 1;
    return {
      value: row.row_key,
      label: duplicateName ? `${row.name} (${platformCodeLabel(row.platform_code)})` : row.name,
    };
  });

  return options.sort((left, right) => left.label.localeCompare(right.label, "ru"));
}

export function collectUniquePlatformOptions(
  rows: CatalogLocationTableRow[],
): Array<{ value: string; label: string }> {
  const codes = new Set<string>();
  for (const row of rows) {
    codes.add(row.platform_code);
  }
  return [...codes]
    .sort((left, right) => platformSortIndex(left) - platformSortIndex(right))
    .map((code) => ({ value: code, label: platformCodeLabel(code) }));
}

export function catalogTablePlatformLabel(code: string): string {
  return platformCodeLabel(code);
}

export function hasActiveCatalogTableFilters(filters: CatalogTableFilters): boolean {
  return (
    filters.cities.size > 0 ||
    filters.regions.size > 0 ||
    filters.locations.size > 0 ||
    filters.platforms.size > 0 ||
    filters.visited !== "all" ||
    filters.excludePaused ||
    filters.locationStatuses.size > 0
  );
}

export function resetCatalogTableFilters(): CatalogTableFilters {
  return {
    cities: new Set(),
    regions: new Set(),
    locations: new Set(),
    platforms: new Set(),
    visited: "all",
    excludePaused: false,
    locationStatuses: new Set(),
  };
}
