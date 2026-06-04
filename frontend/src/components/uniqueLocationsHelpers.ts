import type { MapLocationPlatformVisit, UniqueLocationDetail } from "../lib/api";
import { platformCodeLabel } from "../lib/format";

export type ActivityFilter = "all" | "runs" | "volunteering";
export type PlatformFilter = "all" | string;

export type UniqueLocationRow = {
  location: UniqueLocationDetail;
  activePlatforms: MapLocationPlatformVisit[];
  dates: string[];
};

export type SortKey = "first_visit" | "last_visit" | "name" | "city" | "visit_count";

export const DEFAULT_SORT_BY_ACTIVITY: Record<ActivityFilter, SortKey> = {
  all: "first_visit",
  runs: "first_visit",
  volunteering: "first_visit",
};

const PLATFORM_ORDER = ["five_verst", "s95", "parkrun", "runpark"];

export function matchesActivity(location: UniqueLocationDetail, filter: ActivityFilter): boolean {
  if (filter === "runs") {
    return location.run_count > 0;
  }
  if (filter === "volunteering") {
    return location.volunteer_count > 0;
  }
  return true;
}

export function platformMatchesActivity(
  platform: MapLocationPlatformVisit,
  filter: ActivityFilter,
): boolean {
  if (filter === "runs") {
    return platform.run_dates.length > 0;
  }
  if (filter === "volunteering") {
    return platform.volunteer_dates.length > 0;
  }
  return platform.run_dates.length > 0 || platform.volunteer_dates.length > 0;
}

export function datesForActivity(platform: MapLocationPlatformVisit, filter: ActivityFilter): string[] {
  if (filter === "runs") {
    return [...platform.run_dates];
  }
  if (filter === "volunteering") {
    return [...platform.volunteer_dates];
  }
  return [...new Set([...platform.run_dates, ...platform.volunteer_dates])].sort((left, right) =>
    right.localeCompare(left),
  );
}

export function activePlatformsForLocation(
  location: UniqueLocationDetail,
  activityFilter: ActivityFilter,
): MapLocationPlatformVisit[] {
  return location.platforms
    .filter((platform) => platformMatchesActivity(platform, activityFilter))
    .sort((left, right) => {
      const leftIndex = PLATFORM_ORDER.indexOf(left.platform_code);
      const rightIndex = PLATFORM_ORDER.indexOf(right.platform_code);
      const normalizedLeft = leftIndex === -1 ? PLATFORM_ORDER.length : leftIndex;
      const normalizedRight = rightIndex === -1 ? PLATFORM_ORDER.length : rightIndex;
      if (normalizedLeft !== normalizedRight) {
        return normalizedLeft - normalizedRight;
      }
      return left.platform_code.localeCompare(right.platform_code);
    });
}

export function locationMatchesPlatformFilter(
  location: UniqueLocationDetail,
  activityFilter: ActivityFilter,
  platformFilter: PlatformFilter,
): boolean {
  if (!matchesActivity(location, activityFilter)) {
    return false;
  }
  if (platformFilter === "all") {
    return true;
  }
  return location.platforms.some(
    (platform) =>
      platform.platform_code === platformFilter && platformMatchesActivity(platform, activityFilter),
  );
}

export function datesForLocation(
  location: UniqueLocationDetail,
  activityFilter: ActivityFilter,
): string[] {
  const dates = new Set<string>();
  for (const platform of activePlatformsForLocation(location, activityFilter)) {
    for (const value of datesForActivity(platform, activityFilter)) {
      dates.add(value);
    }
  }
  return [...dates].sort((left, right) => right.localeCompare(left));
}

export function volunteerRolesTooltipLines(location: UniqueLocationDetail): string[] | null {
  const lines: string[] = [];
  const platformsWithRoles = location.platforms.filter(
    (platform) => (platform.volunteer_roles?.length ?? 0) > 0,
  );
  if (platformsWithRoles.length === 0) {
    return null;
  }

  for (const platform of platformsWithRoles) {
    const roles = platform.volunteer_roles ?? [];
    const formattedRoles = roles
      .map((item) => (item.count > 1 ? `${item.role} (${item.count})` : item.role))
      .join(", ");
    if (platformsWithRoles.length > 1) {
      lines.push(`${platformCodeLabel(platform.platform_code)}: ${formattedRoles}`);
    } else {
      lines.push(formattedRoles);
    }
  }
  return lines;
}

export function firstDate(dates: string[]): string | null {
  if (dates.length === 0) {
    return null;
  }
  return dates.reduce((earliest, value) => (value < earliest ? value : earliest));
}

export function lastDate(dates: string[]): string | null {
  if (dates.length === 0) {
    return null;
  }
  return dates.reduce((latest, value) => (value > latest ? value : latest));
}

export function buildUniqueLocationRows(
  locations: UniqueLocationDetail[],
  activityFilter: ActivityFilter,
  platformFilter: PlatformFilter,
  firstVisitSince?: string,
): UniqueLocationRow[] {
  const rows: UniqueLocationRow[] = [];

  for (const location of locations) {
    if (!locationMatchesPlatformFilter(location, activityFilter, platformFilter)) {
      continue;
    }
    const activePlatforms = activePlatformsForLocation(location, activityFilter);
    if (activePlatforms.length === 0) {
      continue;
    }
    const dates = datesForLocation(location, activityFilter);
    if (firstVisitSince) {
      const first = firstDate(dates);
      if (first === null || first < firstVisitSince) {
        continue;
      }
    }
    rows.push({
      location,
      activePlatforms,
      dates,
    });
  }

  return rows;
}

export function visitCountForLocation(
  location: UniqueLocationDetail,
  activityFilter: ActivityFilter,
): number {
  if (activityFilter === "runs") {
    return location.run_count;
  }
  if (activityFilter === "volunteering") {
    return location.volunteer_count;
  }
  return location.run_count + location.volunteer_count;
}

export function sortUniqueLocationRows(
  rows: UniqueLocationRow[],
  sortKey: SortKey,
  sortAsc: boolean,
  activityFilter: ActivityFilter,
): UniqueLocationRow[] {
  const sorted = [...rows];
  sorted.sort((left, right) => {
    let result = 0;

    switch (sortKey) {
      case "first_visit": {
        result = (firstDate(left.dates) ?? "").localeCompare(firstDate(right.dates) ?? "");
        break;
      }
      case "last_visit": {
        result = (lastDate(left.dates) ?? "").localeCompare(lastDate(right.dates) ?? "");
        break;
      }
      case "visit_count": {
        result =
          visitCountForLocation(left.location, activityFilter) -
          visitCountForLocation(right.location, activityFilter);
        break;
      }
      case "city": {
        result = (left.location.city ?? "").localeCompare(right.location.city ?? "", "ru");
        break;
      }
      case "name":
      default: {
        result = left.location.name.localeCompare(right.location.name, "ru");
        break;
      }
    }

    if (result === 0) {
      result = left.location.name.localeCompare(right.location.name, "ru");
    }

    return sortAsc ? result : -result;
  });
  return sorted;
}

export function defaultSortAsc(column: SortKey): boolean {
  if (column === "last_visit" || column === "visit_count") {
    return false;
  }
  return true;
}

export function toggleColumnSort(currentKey: SortKey, currentAsc: boolean, column: SortKey): {
  sortKey: SortKey;
  sortAsc: boolean;
} {
  if (currentKey === column) {
    return { sortKey: column, sortAsc: !currentAsc };
  }
  return { sortKey: column, sortAsc: defaultSortAsc(column) };
}

export function buildPlatformCounts(
  locations: UniqueLocationDetail[],
  activityFilter: ActivityFilter,
  firstVisitSince?: string,
): Array<{ platform_code: string; location_count: number }> {
  const counts = new Map<string, Set<string>>();

  for (const location of locations) {
    if (!matchesActivity(location, activityFilter)) {
      continue;
    }
    const dates = datesForLocation(location, activityFilter);
    if (firstVisitSince) {
      const first = firstDate(dates);
      if (first === null || first < firstVisitSince) {
        continue;
      }
    }
    for (const platform of location.platforms) {
      if (!platformMatchesActivity(platform, activityFilter)) {
        continue;
      }
      const bucket = counts.get(platform.platform_code) ?? new Set<string>();
      bucket.add(location.catalog_identity_key);
      counts.set(platform.platform_code, bucket);
    }
  }

  return [...counts.entries()]
    .map(([platform_code, keys]) => ({ platform_code, location_count: keys.size }))
    .sort((left, right) => {
      const leftIndex = PLATFORM_ORDER.indexOf(left.platform_code);
      const rightIndex = PLATFORM_ORDER.indexOf(right.platform_code);
      const normalizedLeft = leftIndex === -1 ? PLATFORM_ORDER.length : leftIndex;
      const normalizedRight = rightIndex === -1 ? PLATFORM_ORDER.length : rightIndex;
      if (normalizedLeft !== normalizedRight) {
        return normalizedLeft - normalizedRight;
      }
      return left.platform_code.localeCompare(right.platform_code);
    });
}

export function uniqueLocationCountForActivity(
  data: { total_locations: number; unique_run_locations: number; unique_volunteer_locations: number },
  activityFilter: ActivityFilter,
): number {
  if (activityFilter === "runs") {
    return data.unique_run_locations;
  }
  if (activityFilter === "volunteering") {
    return data.unique_volunteer_locations;
  }
  return data.total_locations;
}

export function modalTitleForActivity(filter: ActivityFilter, firstVisitSince?: string): string {
  if (firstVisitSince) {
    return "Новые локации за 12 мес.";
  }
  if (filter === "runs") {
    return "Локации с пробежками";
  }
  if (filter === "volunteering") {
    return "Локации с волонтёрством";
  }
  return "Уникальные локации";
}

export function firstVisitColumnLabel(filter: ActivityFilter): string {
  if (filter === "runs") {
    return "Первая пробежка";
  }
  if (filter === "volunteering") {
    return "Первое волонтёрство";
  }
  return "Первый визит";
}

export function lastVisitColumnLabel(filter: ActivityFilter): string {
  if (filter === "runs") {
    return "Последняя пробежка";
  }
  if (filter === "volunteering") {
    return "Последнее волонтёрство";
  }
  return "Последний визит";
}

export function platformLabel(code: string): string {
  return platformCodeLabel(code);
}
