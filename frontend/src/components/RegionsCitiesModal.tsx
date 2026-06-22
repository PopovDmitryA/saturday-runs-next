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

type GeoGroup = {
  name: string;
  locationNames: string[];
};

function buildGroups(
  data: UniqueLocationsDetailResponse,
  activityFilter: ActivityFilter,
  groupBy: GroupBy,
): GeoGroup[] {
  const map = new Map<string, string[]>();

  for (const loc of data.locations) {
    if (!matchesActivity(loc, activityFilter)) {
      continue;
    }
    const rawKey = groupBy === "region" ? (loc.region ?? null) : loc.city;
    const name = rawKey ?? (groupBy === "region" ? "Не указан" : "Не указан");

    let names = map.get(name);
    if (!names) {
      names = [];
      map.set(name, names);
    }
    names.push(loc.name);
  }

  return Array.from(map.entries())
    .map(([name, locationNames]) => ({ name, locationNames: locationNames.sort() }))
    .sort((a, b) => {
      if (b.locationNames.length !== a.locationNames.length) {
        return b.locationNames.length - a.locationNames.length;
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
  const count = groups.length;
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
              <th>Локации</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => (
              <tr key={group.name}>
                <td className="geo-groups-name">{group.name}</td>
                <td className="col-count">{group.locationNames.length}</td>
                <td className="geo-groups-locations muted">{group.locationNames.join(", ")}</td>
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
