import { useEffect, useMemo, useState } from "react";
import {
  applyActivityFilters,
  buildLocationOptions,
  buildPlatformOptions,
  createFullSelection,
  isDateFilterActive,
  isFilterActive,
  isFullSelection,
  uniqueLocations,
  uniquePlatforms,
  type ActivityItem,
  type ActivitySort,
} from "../lib/activityList";

const DEFAULT_SORT: ActivitySort = "date_desc";

/**
 * Локация из адреса (?location=Коломенское) — переход «Мои пробежки здесь»
 * со страницы локации сразу открывает список с этим фильтром.
 */
function locationFromQuery(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  const value = new URLSearchParams(window.location.search).get("location");
  return value && value.trim() ? value.trim() : null;
}

export function useActivityFilters<T extends ActivityItem>(items: T[]) {
  const [sort, setSort] = useState<ActivitySort>(DEFAULT_SORT);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [selectedPlatforms, setSelectedPlatforms] = useState<Set<string>>(() => new Set());
  const [selectedLocations, setSelectedLocations] = useState<Set<string>>(() => new Set());

  const allPlatforms = useMemo(() => uniquePlatforms(items), [items]);
  const allLocations = useMemo(() => uniqueLocations(items), [items]);
  const platformOptions = useMemo(() => buildPlatformOptions(items), [items]);
  const locationOptions = useMemo(() => buildLocationOptions(items), [items]);

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

  // Предвыбор из адреса применяем один раз, когда список локаций загрузился.
  const [queryLocation, setQueryLocation] = useState<string | null>(locationFromQuery);

  useEffect(() => {
    if (queryLocation === null || allLocations.length === 0) {
      return;
    }
    const match = allLocations.find(
      (name) => name.toLowerCase() === queryLocation.toLowerCase(),
    );
    setQueryLocation(null);
    if (match) {
      setSelectedLocations(new Set([match]));
    }
  }, [queryLocation, allLocations]);

  useEffect(() => {
    if (queryLocation !== null) {
      return;
    }
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
  }, [allLocations, queryLocation]);

  const filtered = useMemo(
    () =>
      applyActivityFilters(
        items,
        allPlatforms,
        selectedPlatforms,
        allLocations,
        selectedLocations,
        dateFrom,
        dateTo,
      ),
    [items, allPlatforms, selectedPlatforms, allLocations, selectedLocations, dateFrom, dateTo],
  );

  const hasActiveFilters =
    isDateFilterActive(dateFrom, dateTo) ||
    isFilterActive(selectedPlatforms, allPlatforms) ||
    isFilterActive(selectedLocations, allLocations);

  const resetFilters = () => {
    setDateFrom("");
    setDateTo("");
    setSelectedPlatforms(createFullSelection(allPlatforms));
    setSelectedLocations(createFullSelection(allLocations));
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
    platformOptions,
    locationOptions,
    filtered,
    hasActiveFilters,
    resetFilters,
    resetAll,
    platformFilterActive: isFilterActive(selectedPlatforms, allPlatforms),
    locationFilterActive: isFilterActive(selectedLocations, allLocations),
    dateFilterActive: isDateFilterActive(dateFrom, dateTo),
  };
}
