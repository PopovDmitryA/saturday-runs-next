import { useEffect, useMemo, useState } from "react";
import type { UniqueLocationsDetailResponse } from "../lib/api";
import { useAppDataSource } from "../lib/appDataSource";
import { pluralizeRu } from "../lib/format";
import { matchesActivity, type ActivityFilter } from "./uniqueLocationsHelpers";
import { DetailModal } from "./DetailModal";

export type GroupBy = "region" | "city";

type RegionsCitiesModalProps = {
  open: boolean;
  onClose: () => void;
  activityFilter: ActivityFilter;
  groupBy: GroupBy;
};

const UNKNOWN_GEO = "Не заполнено";

type GeoLocationEntry = {
  name: string;
  hint: string | null;
  visitCount: number;
  isForeign: boolean;
};

type GeoGroup = {
  name: string;
  locations: GeoLocationEntry[];
  isUnknown: boolean;
  isForeign: boolean;
  totalVisits: number;
};

function locationVisitCount(loc: UniqueLocationsDetailResponse["locations"][number], activityFilter: ActivityFilter): number {
  if (activityFilter === "runs") return loc.run_count;
  if (activityFilter === "volunteering") return loc.volunteer_count;
  return loc.run_count + loc.volunteer_count;
}

function buildGroups(
  data: UniqueLocationsDetailResponse,
  activityFilter: ActivityFilter,
  groupBy: GroupBy,
): GeoGroup[] {
  const map = new Map<string, GeoLocationEntry[]>();

  for (const loc of data.locations) {
    if (!matchesActivity(loc, activityFilter)) {
      continue;
    }
    const rawKey = groupBy === "region" ? (loc.region ?? null) : loc.city;
    const name = rawKey ?? UNKNOWN_GEO;
    const isUnknown = rawKey === null;
    const hint = isUnknown ? (groupBy === "region" ? loc.city : loc.region) ?? null : null;

    let entries = map.get(name);
    if (!entries) {
      entries = [];
      map.set(name, entries);
    }
    entries.push({
      name: loc.name,
      hint,
      visitCount: locationVisitCount(loc, activityFilter),
      isForeign: Boolean(loc.is_foreign),
    });
  }

  return Array.from(map.entries())
    .map(([name, locations]) => ({
      name,
      locations: locations.sort((a, b) => a.name.localeCompare(b.name, "ru")),
      isUnknown: name === UNKNOWN_GEO,
      isForeign: locations.every((l) => l.isForeign),
      totalVisits: locations.reduce((sum, l) => sum + l.visitCount, 0),
    }))
    .sort((a, b) => {
      // Зарубежные регионы/города — всегда в самом низу списка, даже ниже
      // группы "Не заполнено".
      if (a.isForeign !== b.isForeign) return a.isForeign ? 1 : -1;
      if (a.isUnknown !== b.isUnknown) return a.isUnknown ? 1 : -1;
      if (b.locations.length !== a.locations.length) {
        return b.locations.length - a.locations.length;
      }
      return a.name.localeCompare(b.name, "ru");
    });
}

function modalTitle(groupBy: GroupBy, activityFilter: ActivityFilter): string {
  const geo = groupBy === "region" ? "Регионы" : "Города";
  if (activityFilter === "runs") return `${geo} с пробежками`;
  if (activityFilter === "volunteering") return `${geo} с волонтёрством`;
  return geo;
}

function summaryText(groups: GeoGroup[], groupBy: GroupBy): string {
  const count = groups.filter((g) => !g.isUnknown).length;
  if (groupBy === "region") {
    return pluralizeRu(count, ["регион", "региона", "регионов"]);
  }
  return pluralizeRu(count, ["город", "города", "городов"]);
}

function ModalContent({
  data,
  activityFilter,
  groupBy,
}: {
  data: UniqueLocationsDetailResponse;
  activityFilter: ActivityFilter;
  groupBy: GroupBy;
}) {
  const groups = useMemo(
    () => buildGroups(data, activityFilter, groupBy),
    [data, activityFilter, groupBy],
  );

  if (groups.length === 0) {
    return <p className="muted unique-locations-empty">Нет данных.</p>;
  }

  const colLabel = groupBy === "region" ? "Регион" : "Город";

  return (
    <>
      <div className="unique-locations-summary muted">{summaryText(groups, groupBy)}</div>
      <div className="unique-locations-table-wrap">
        <table className="data-table unique-locations-table geo-groups-table">
          <thead>
            <tr>
              <th>{colLabel}</th>
              <th className="col-count">Локаций</th>
              <th className="col-count">Визитов</th>
              <th>Локации</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => (
              <tr key={group.name} className={group.isUnknown ? "geo-groups-row-unknown" : undefined}>
                <td className="geo-groups-name">{group.name}</td>
                <td className="col-count">{group.locations.length}</td>
                <td className="col-count">{group.totalVisits}</td>
                <td className="geo-groups-locations muted">
                  {group.isUnknown
                    ? group.locations.map((loc, i) => (
                        <span key={loc.name}>
                          {i > 0 && ", "}
                          {loc.name}
                          {loc.hint && (
                            <span className="geo-groups-hint"> ({loc.hint})</span>
                          )}
                          {loc.visitCount > 1 && (
                            <span className="geo-groups-visit-count"> ×{loc.visitCount}</span>
                          )}
                        </span>
                      ))
                    : group.locations.map((loc, i) => (
                        <span key={loc.name}>
                          {i > 0 && ", "}
                          {loc.name}
                          {loc.visitCount > 1 && (
                            <span className="geo-groups-visit-count"> ×{loc.visitCount}</span>
                          )}
                        </span>
                      ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function RegionsCitiesModal({
  open,
  onClose,
  activityFilter,
  groupBy,
}: RegionsCitiesModalProps) {
  const { getUniqueLocationsDetail } = useAppDataSource();
  const [data, setData] = useState<UniqueLocationsDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    getUniqueLocationsDetail(false)
      .then((response) => {
        if (!cancelled) setData(response);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Не удалось загрузить данные");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, getUniqueLocationsDetail]);

  return (
    <DetailModal open={open} title={modalTitle(groupBy, activityFilter)} onClose={onClose}>
      {loading && <p className="muted">Загрузка…</p>}
      {error && <p className="error-text">{error}</p>}
      {!loading && !error && data && (
        <ModalContent data={data} activityFilter={activityFilter} groupBy={groupBy} />
      )}
    </DetailModal>
  );
}
