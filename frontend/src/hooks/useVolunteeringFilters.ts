import { useEffect, useMemo, useState } from "react";
import type { VolunteeringItem } from "../lib/api";
import {
  applyVolunteeringFilters,
  buildLocationOptions,
  buildPlatformOptions,
  buildRoleOptions,
  createFullSelection,
  isDateFilterActive,
  isFilterActive,
  isFullSelection,
  uniqueLocations,
  uniquePlatforms,
  uniqueRoles,
  type ActivitySort,
} from "../lib/activityList";

const DEFAULT_SORT: ActivitySort = "date_desc";

export function useVolunteeringFilters(items: VolunteeringItem[]) {
  const [sort, setSort] = useState<ActivitySort>(DEFAULT_SORT);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [selectedPlatforms, setSelectedPlatforms] = useState<Set<string>>(() => new Set());
  const [selectedLocations, setSelectedLocations] = useState<Set<string>>(() => new Set());
  const [selectedRoles, setSelectedRoles] = useState<Set<string>>(() => new Set());

  const allPlatforms = useMemo(() => uniquePlatforms(items), [items]);
  const allLocations = useMemo(() => uniqueLocations(items), [items]);
  const allRoles = useMemo(() => uniqueRoles(items), [items]);
  const platformOptions = useMemo(() => buildPlatformOptions(items), [items]);
  const locationOptions = useMemo(() => buildLocationOptions(items), [items]);
  const roleOptions = useMemo(() => buildRoleOptions(items), [items]);

  useEffect(() => {
    setSelectedPlatforms((prev) => {
      if (prev.size === 0 || isFullSelection(prev, allPlatforms)) {
        return createFullSelection(allPlatforms);
      }
      const next = new Set<string>();
      for (const code of allPlatforms) {
        if (prev.has(code)) {
          next.add(code);
        }
      }
      return next.size > 0 ? next : createFullSelection(allPlatforms);
    });
  }, [allPlatforms]);

  useEffect(() => {
    setSelectedLocations((prev) => {
      if (prev.size === 0 || isFullSelection(prev, allLocations)) {
        return createFullSelection(allLocations);
      }
      const next = new Set<string>();
      for (const name of allLocations) {
        if (prev.has(name)) {
          next.add(name);
        }
      }
      return next.size > 0 ? next : createFullSelection(allLocations);
    });
  }, [allLocations]);

  useEffect(() => {
    setSelectedRoles((prev) => {
      if (prev.size === 0 || isFullSelection(prev, allRoles)) {
        return createFullSelection(allRoles);
      }
      const next = new Set<string>();
      for (const role of allRoles) {
        if (prev.has(role)) {
          next.add(role);
        }
      }
      return next.size > 0 ? next : createFullSelection(allRoles);
    });
  }, [allRoles]);

  const filtered = useMemo(
    () =>
      applyVolunteeringFilters(
        items,
        allPlatforms,
        selectedPlatforms,
        allLocations,
        selectedLocations,
        allRoles,
        selectedRoles,
        dateFrom,
        dateTo,
      ),
    [
      items,
      allPlatforms,
      selectedPlatforms,
      allLocations,
      selectedLocations,
      allRoles,
      selectedRoles,
      dateFrom,
      dateTo,
    ],
  );

  const hasActiveFilters =
    isDateFilterActive(dateFrom, dateTo) ||
    isFilterActive(selectedPlatforms, allPlatforms) ||
    isFilterActive(selectedLocations, allLocations) ||
    isFilterActive(selectedRoles, allRoles);

  const resetFilters = () => {
    setDateFrom("");
    setDateTo("");
    setSelectedPlatforms(createFullSelection(allPlatforms));
    setSelectedLocations(createFullSelection(allLocations));
    setSelectedRoles(createFullSelection(allRoles));
  };

  const resetAll = () => {
    resetFilters();
    setSort(DEFAULT_SORT);
  };

  return {
    sort,
    setSort,
    dateFrom,
    setDateFrom,
    dateTo,
    setDateTo,
    selectedPlatforms,
    setSelectedPlatforms,
    selectedLocations,
    setSelectedLocations,
    selectedRoles,
    setSelectedRoles,
    platformOptions,
    locationOptions,
    roleOptions,
    filtered,
    hasActiveFilters,
    resetFilters,
    resetAll,
    platformFilterActive: isFilterActive(selectedPlatforms, allPlatforms),
    locationFilterActive: isFilterActive(selectedLocations, allLocations),
    roleFilterActive: isFilterActive(selectedRoles, allRoles),
    dateFilterActive: isDateFilterActive(dateFrom, dateTo),
  };
}
