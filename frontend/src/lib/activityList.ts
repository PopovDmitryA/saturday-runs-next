import type { RunItem, VolunteeringItem } from "./api";
import { platformCodeLabel } from "./format";

export type ActivitySort =
  | "date_desc"
  | "date_asc"
  | "finish_asc"
  | "finish_desc"
  | "pace_asc"
  | "pace_desc"
  | "position_asc"
  | "position_desc";

export type ActivityItem = {
  platform_code: string;
  event_date: string;
  location_name: string;
};

function compareDates(a: string, b: string): number {
  return a.localeCompare(b);
}

function finishSortKey(run: RunItem): number {
  if (run.finish_time_sec != null) {
    return run.finish_time_sec;
  }
  return Number.MAX_SAFE_INTEGER;
}

function paceSortKey(run: RunItem): number {
  if (run.pace_sec_per_km != null) {
    return run.pace_sec_per_km;
  }
  return Number.MAX_SAFE_INTEGER;
}

function positionSortKey(run: RunItem): number {
  if (run.position != null) {
    return run.position;
  }
  return Number.MAX_SAFE_INTEGER;
}

export function uniquePlatforms(items: ActivityItem[]): string[] {
  return [...new Set(items.map((item) => item.platform_code))].sort();
}

export function uniqueLocations(items: ActivityItem[]): string[] {
  return [...new Set(items.map((item) => item.location_name))].sort((a, b) => a.localeCompare(b, "ru"));
}

export function buildPlatformOptions(items: ActivityItem[]) {
  return uniquePlatforms(items).map((code) => ({
    value: code,
    label: platformCodeLabel(code),
  }));
}

export function buildLocationOptions(items: ActivityItem[]) {
  return uniqueLocations(items).map((name) => ({
    value: name,
    label: name,
  }));
}

export function createFullSelection(values: string[]): Set<string> {
  return new Set(values);
}

export function isFullSelection(selected: Set<string>, allValues: string[]): boolean {
  return allValues.length === 0 || selected.size === 0 || selected.size >= allValues.length;
}

export function isFilterActive(selected: Set<string>, allValues: string[]): boolean {
  return allValues.length > 0 && selected.size > 0 && selected.size < allValues.length;
}

export function filterBySelection<T>(
  items: T[],
  allValues: string[],
  selected: Set<string>,
  pickValue: (item: T) => string,
): T[] {
  if (isFullSelection(selected, allValues)) {
    return items;
  }
  return items.filter((item) => selected.has(pickValue(item)));
}

export function filterByDateRange<T extends { event_date: string }>(
  items: T[],
  dateFrom: string,
  dateTo: string,
): T[] {
  return items.filter((item) => {
    if (dateFrom && item.event_date < dateFrom) {
      return false;
    }
    if (dateTo && item.event_date > dateTo) {
      return false;
    }
    return true;
  });
}

export function isDateFilterActive(dateFrom: string, dateTo: string): boolean {
  return dateFrom !== "" || dateTo !== "";
}

export function applyActivityFilters<T extends ActivityItem>(
  items: T[],
  allPlatforms: string[],
  selectedPlatforms: Set<string>,
  allLocations: string[],
  selectedLocations: Set<string>,
  dateFrom: string,
  dateTo: string,
): T[] {
  let result = filterBySelection(items, allPlatforms, selectedPlatforms, (item) => item.platform_code);
  result = filterBySelection(result, allLocations, selectedLocations, (item) => item.location_name);
  return filterByDateRange(result, dateFrom, dateTo);
}

export function uniqueRoles(items: VolunteeringItem[]): string[] {
  return [...new Set(items.map((item) => item.role?.trim()).filter((role): role is string => Boolean(role)))].sort(
    (a, b) => a.localeCompare(b, "ru"),
  );
}

export function buildRoleOptions(items: VolunteeringItem[]) {
  return uniqueRoles(items).map((role) => ({
    value: role,
    label: role,
  }));
}

export function applyVolunteeringFilters(
  items: VolunteeringItem[],
  allPlatforms: string[],
  selectedPlatforms: Set<string>,
  allLocations: string[],
  selectedLocations: Set<string>,
  allRoles: string[],
  selectedRoles: Set<string>,
  dateFrom: string,
  dateTo: string,
): VolunteeringItem[] {
  let result = applyActivityFilters(
    items,
    allPlatforms,
    selectedPlatforms,
    allLocations,
    selectedLocations,
    dateFrom,
    dateTo,
  );
  if (!isFullSelection(selectedRoles, allRoles)) {
    result = result.filter((item) => {
      const role = item.role?.trim();
      return Boolean(role) && selectedRoles.has(role as string);
    });
  }
  return result;
}

export function sortRuns(items: RunItem[], sort: ActivitySort): RunItem[] {
  const sorted = [...items];
  switch (sort) {
    case "date_asc":
      sorted.sort((a, b) => compareDates(a.event_date, b.event_date));
      break;
    case "finish_asc":
      sorted.sort((a, b) => {
        const byTime = finishSortKey(a) - finishSortKey(b);
        return byTime !== 0 ? byTime : compareDates(b.event_date, a.event_date);
      });
      break;
    case "finish_desc":
      sorted.sort((a, b) => {
        const aKey = a.finish_time_sec;
        const bKey = b.finish_time_sec;
        if (aKey == null && bKey == null) {
          return compareDates(b.event_date, a.event_date);
        }
        if (aKey == null) {
          return 1;
        }
        if (bKey == null) {
          return -1;
        }
        const byTime = bKey - aKey;
        return byTime !== 0 ? byTime : compareDates(b.event_date, a.event_date);
      });
      break;
    case "pace_asc":
      sorted.sort((a, b) => {
        const byPace = paceSortKey(a) - paceSortKey(b);
        return byPace !== 0 ? byPace : compareDates(b.event_date, a.event_date);
      });
      break;
    case "pace_desc":
      sorted.sort((a, b) => {
        const aKey = a.pace_sec_per_km;
        const bKey = b.pace_sec_per_km;
        if (aKey == null && bKey == null) {
          return compareDates(b.event_date, a.event_date);
        }
        if (aKey == null) {
          return 1;
        }
        if (bKey == null) {
          return -1;
        }
        const byPace = bKey - aKey;
        return byPace !== 0 ? byPace : compareDates(b.event_date, a.event_date);
      });
      break;
    case "position_asc":
      sorted.sort((a, b) => {
        const byPosition = positionSortKey(a) - positionSortKey(b);
        return byPosition !== 0 ? byPosition : compareDates(b.event_date, a.event_date);
      });
      break;
    case "position_desc":
      sorted.sort((a, b) => {
        const aKey = a.position;
        const bKey = b.position;
        if (aKey == null && bKey == null) {
          return compareDates(b.event_date, a.event_date);
        }
        if (aKey == null) {
          return 1;
        }
        if (bKey == null) {
          return -1;
        }
        const byPosition = bKey - aKey;
        return byPosition !== 0 ? byPosition : compareDates(b.event_date, a.event_date);
      });
      break;
    case "date_desc":
    default:
      sorted.sort((a, b) => compareDates(b.event_date, a.event_date));
      break;
  }
  return sorted;
}

export function sortVolunteering(items: VolunteeringItem[], sort: ActivitySort): VolunteeringItem[] {
  const sorted = [...items];
  if (sort === "date_asc") {
    sorted.sort((a, b) => compareDates(a.event_date, b.event_date));
  } else {
    sorted.sort((a, b) => compareDates(b.event_date, a.event_date));
  }
  return sorted;
}

export function toggleDateSort(sort: ActivitySort): ActivitySort {
  return sort === "date_asc" ? "date_desc" : "date_asc";
}

export function toggleFinishSort(sort: ActivitySort): ActivitySort {
  return sort === "finish_asc" ? "finish_desc" : "finish_asc";
}

export function togglePaceSort(sort: ActivitySort): ActivitySort {
  return sort === "pace_asc" ? "pace_desc" : "pace_asc";
}

export function togglePositionSort(sort: ActivitySort): ActivitySort {
  return sort === "position_asc" ? "position_desc" : "position_asc";
}
