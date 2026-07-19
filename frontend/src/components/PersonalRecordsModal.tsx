import { useEffect, useMemo, useState } from "react";
import { type PersonalRecordItem } from "../lib/api";
import { useAppDataSource } from "../lib/appDataSource";
import { formatFinishTimeValue, platformCodeLabel } from "../lib/format";
import { ActivityDateLink } from "./ActivityDateLink";
import { ColumnHeader } from "./activityTable/ColumnHeader";
import { GlobalPrFinishTime } from "./GlobalPrFinishTime";
import { LocationPrLocationName } from "./LocationPrLocationName";
import { PlatformBadge } from "./PlatformBadge";
import { DetailModal } from "./DetailModal";

type PersonalRecordsModalProps = {
  open: boolean;
  onClose: () => void;
  includeTest?: boolean;
};

type PlatformFilter = "all" | string;
type SortKey = "date" | "location" | "result";

const PLATFORM_ORDER = ["five_verst", "s95", "parkrun", "runpark"];

function formatResult(item: PersonalRecordItem): string {
  return formatFinishTimeValue(item.finish_time_display, item.finish_time_sec);
}

function platformSortIndex(code: string): number {
  const index = PLATFORM_ORDER.indexOf(code);
  return index === -1 ? PLATFORM_ORDER.length : index;
}

function buildPlatformCounts(items: PersonalRecordItem[]): Array<{ platform_code: string; count: number }> {
  const counts = new Map<string, number>();
  for (const item of items) {
    counts.set(item.platform_code, (counts.get(item.platform_code) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([platform_code, count]) => ({ platform_code, count }))
    .sort((left, right) => {
      const leftIndex = platformSortIndex(left.platform_code);
      const rightIndex = platformSortIndex(right.platform_code);
      if (leftIndex !== rightIndex) {
        return leftIndex - rightIndex;
      }
      return left.platform_code.localeCompare(right.platform_code);
    });
}

function sortPersonalRecords(
  items: PersonalRecordItem[],
  sortKey: SortKey,
  sortAsc: boolean,
  platformFilter: PlatformFilter,
): PersonalRecordItem[] {
  const filtered =
    platformFilter === "all" ? items : items.filter((item) => item.platform_code === platformFilter);

  const sorted = [...filtered].sort((left, right) => {
    const dateDiff = right.event_date.localeCompare(left.event_date);
    if (dateDiff !== 0) {
      return dateDiff;
    }
    return platformSortIndex(left.platform_code) - platformSortIndex(right.platform_code);
  });

  if (platformFilter !== "all") {
    if (sortKey === "date") {
      return sortAsc ? [...sorted].reverse() : sorted;
    }
    const customSorted = [...filtered].sort((left, right) => {
      if (sortKey === "location") {
        const nameDiff = left.location_name.localeCompare(right.location_name, "ru");
        if (nameDiff !== 0) {
          return nameDiff;
        }
        return right.event_date.localeCompare(left.event_date);
      }
      const leftSec = left.finish_time_sec ?? Number.MAX_SAFE_INTEGER;
      const rightSec = right.finish_time_sec ?? Number.MAX_SAFE_INTEGER;
      if (leftSec !== rightSec) {
        return leftSec - rightSec;
      }
      return right.event_date.localeCompare(left.event_date);
    });
    return sortAsc ? customSorted : customSorted.reverse();
  }

  return sorted;
}

export function PersonalRecordsModal({ open, onClose, includeTest = false }: PersonalRecordsModalProps) {
  const { getPersonalRecords: fetchPersonalRecords } = useAppDataSource();
  const [items, setItems] = useState<PersonalRecordItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [platformFilter, setPlatformFilter] = useState<PlatformFilter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("date");
  const [sortAsc, setSortAsc] = useState(false);

  useEffect(() => {
    if (!open) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPersonalRecords(includeTest)
      .then((data) => {
        if (!cancelled) {
          setItems(data);
          setPlatformFilter("all");
          setSortKey("date");
          setSortAsc(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Не удалось загрузить PR-пробежки");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open, includeTest, fetchPersonalRecords]);

  const platformCounts = useMemo(() => buildPlatformCounts(items), [items]);
  const rows = useMemo(
    () => sortPersonalRecords(items, sortKey, sortAsc, platformFilter),
    [items, sortKey, sortAsc, platformFilter],
  );

  const handleSort = (key: SortKey) => {
    if (platformFilter === "all") {
      return;
    }
    if (sortKey === key) {
      setSortAsc((current) => !current);
      return;
    }
    setSortKey(key);
    setSortAsc(key === "location");
  };

  return (
    <DetailModal open={open} title="PR-пробежки" onClose={onClose}>
      {loading && <p className="muted">Загрузка…</p>}
      {error && <p className="error-text">{error}</p>}
      {!loading && !error && items.length === 0 && (
        <p className="muted unique-locations-empty">Нет пробежек с личным рекордом.</p>
      )}
      {!loading && !error && items.length > 0 && (
        <>
          <p className="muted personal-records-hint">
            Обновления ваших рекордов: бейдж «PR» — личный рекорд в системе (улучшение лучшего
            времени в этой системе; дебютный старт — точка отсчёта — тоже считается), оранжевое
            время — глобальный рекорд по всем системам на момент той пробежки, подсвеченная
            локация — рекорд этой площадки среди всех систем.
          </p>

          <div className="unique-locations-filters" role="tablist" aria-label="Фильтр по системам">
            <button
              type="button"
              role="tab"
              aria-selected={platformFilter === "all"}
              className={platformFilter === "all" ? "map-mode-tab active" : "map-mode-tab"}
              onClick={() => setPlatformFilter("all")}
            >
              Все ({items.length})
            </button>
            {platformCounts.map((item) => (
              <button
                key={item.platform_code}
                type="button"
                role="tab"
                aria-selected={platformFilter === item.platform_code}
                className={platformFilter === item.platform_code ? "map-mode-tab active" : "map-mode-tab"}
                onClick={() => {
                  setPlatformFilter(item.platform_code);
                  setSortKey("date");
                  setSortAsc(false);
                }}
              >
                {platformCodeLabel(item.platform_code)} ({item.count})
              </button>
            ))}
          </div>

          {rows.length === 0 ? (
            <p className="muted unique-locations-empty">Нет PR-пробежек для выбранной системы.</p>
          ) : (
            <div className="unique-locations-table-wrap">
              <table className="data-table unique-locations-table best-results-table personal-records-table">
                <thead>
                  <tr>
                    {platformFilter === "all" ? (
                      <>
                        <th>Дата</th>
                        <th>Локация</th>
                        <th>Система</th>
                        <th>Результат</th>
                      </>
                    ) : (
                      <>
                        <ColumnHeader
                          label="Дата"
                          filterable={false}
                          sortActive={sortKey === "date"}
                          sortAsc={sortAsc}
                          onSort={() => handleSort("date")}
                        />
                        <ColumnHeader
                          label="Локация"
                          filterable={false}
                          sortActive={sortKey === "location"}
                          sortAsc={sortAsc}
                          onSort={() => handleSort("location")}
                        />
                        <th>Система</th>
                        <ColumnHeader
                          label="Результат"
                          filterable={false}
                          sortActive={sortKey === "result"}
                          sortAsc={sortAsc}
                          onSort={() => handleSort("result")}
                        />
                      </>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((item, index) => (
                    <tr key={`${item.platform_code}-${item.event_date}-${item.location_name}-${index}`}>
                      <td className="col-date">
                        <ActivityDateLink date={item.event_date} url={item.event_url} />
                        {item.is_pr && <span className="badge badge-pr">PR</span>}
                      </td>
                      <td className="col-location">
                        <LocationPrLocationName isLocationPr={item.is_location_pr ?? false}>
                          <span className="unique-locations-name">{item.location_name}</span>
                        </LocationPrLocationName>
                      </td>
                      <td className="col-platform">
                        <PlatformBadge code={item.platform_code} />
                      </td>
                      <td className="col-result">
                        <GlobalPrFinishTime isGlobalPr={item.is_global_pr}>
                          {formatResult(item)}
                        </GlobalPrFinishTime>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </DetailModal>
  );
}
