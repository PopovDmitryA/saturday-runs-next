import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { type UniqueLocationsDetailResponse } from "../lib/api";
import { useAppDataSource } from "../lib/appDataSource";
import {
  formatDate,
  locationsWithRunsSummary,
  locationsWithVolunteeringSummary,
  uniqueLocationsLabel,
} from "../lib/format";
import { ColumnHeader, PlainColumnHeader } from "./activityTable/ColumnHeader";
import { LocationNameLink } from "./LocationNameLink";
import { PlatformBadge } from "./PlatformBadge";
import { DetailModal } from "./DetailModal";
import {
  buildUniqueLocationRows,
  buildPlatformCounts,
  DEFAULT_SORT_BY_ACTIVITY,
  defaultSortAsc,
  firstDate,
  firstVisitColumnLabel,
  volunteerRolesTooltipLines,
  modalTitleForActivity,
  platformLabel,
  sortUniqueLocationRows,
  toggleColumnSort,
  uniqueLocationCountForActivity,
  type ActivityFilter,
  type PlatformFilter,
  type SortKey,
  type UniqueLocationRow,
} from "./uniqueLocationsHelpers";

type UniqueLocationsModalProps = {
  open: boolean;
  onClose: () => void;
  includeTest?: boolean;
  activityFilter?: ActivityFilter;
  firstVisitSince?: string;
  fetchDetail?: (includeTest: boolean) => Promise<UniqueLocationsDetailResponse>;
};

function VisitCountTooltip({ lines, children }: { lines: string[]; children: ReactNode }) {
  const triggerRef = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(false);
  const [position, setPosition] = useState({ x: 0, y: 0 });

  const updatePosition = () => {
    const trigger = triggerRef.current;
    if (!trigger) {
      return;
    }
    const rect = trigger.getBoundingClientRect();
    setPosition({
      x: rect.left + rect.width / 2,
      y: rect.top - 8,
    });
  };

  const show = () => {
    updatePosition();
    setVisible(true);
  };

  const hide = () => {
    setVisible(false);
  };

  useEffect(() => {
    if (!visible) {
      return;
    }
    const handleReposition = () => updatePosition();
    window.addEventListener("scroll", handleReposition, true);
    window.addEventListener("resize", handleReposition);
    return () => {
      window.removeEventListener("scroll", handleReposition, true);
      window.removeEventListener("resize", handleReposition);
    };
  }, [visible]);

  return (
    <>
      <span
        ref={triggerRef}
        className="unique-locations-count-tip-trigger"
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        tabIndex={0}
      >
        {children}
      </span>
      {visible &&
        createPortal(
          <div
            className="unique-locations-count-tooltip"
            role="tooltip"
            style={{ left: position.x, top: position.y }}
          >
            {lines.map((line) => (
              <span key={line} className="unique-locations-count-tooltip-line">
                {line}
              </span>
            ))}
          </div>,
          document.body,
        )}
    </>
  );
}

function VisitCountCell({ row, activityFilter }: { row: UniqueLocationRow; activityFilter: ActivityFilter }) {
  const count = row.visitCount;
  if (count === 0) {
    return <>—</>;
  }
  if (activityFilter === "runs") {
    return <span>{count}</span>;
  }
  const tooltipLines = volunteerRolesTooltipLines(row.location);
  if (tooltipLines) {
    return <VisitCountTooltip lines={tooltipLines}>{count}</VisitCountTooltip>;
  }
  return <span>{count}</span>;
}

function ModalContent({
  data,
  platformFilter,
  activityFilter,
  firstVisitSince,
  onPlatformFilterChange,
}: {
  data: UniqueLocationsDetailResponse;
  platformFilter: PlatformFilter;
  activityFilter: ActivityFilter;
  firstVisitSince?: string;
  onPlatformFilterChange: (value: PlatformFilter) => void;
}) {
  const [sortKey, setSortKey] = useState<SortKey>(DEFAULT_SORT_BY_ACTIVITY[activityFilter]);
  const [sortAsc, setSortAsc] = useState(defaultSortAsc(DEFAULT_SORT_BY_ACTIVITY[activityFilter]));

  useEffect(() => {
    const defaultKey = DEFAULT_SORT_BY_ACTIVITY[activityFilter];
    setSortKey(defaultKey);
    setSortAsc(defaultSortAsc(defaultKey));
  }, [activityFilter, platformFilter]);

  const handleSort = (column: SortKey) => {
    const next = toggleColumnSort(sortKey, sortAsc, column);
    setSortKey(next.sortKey);
    setSortAsc(next.sortAsc);
  };

  const platformCounts = useMemo(
    () => buildPlatformCounts(data.locations, activityFilter, firstVisitSince),
    [activityFilter, data.locations, firstVisitSince],
  );

  const rows = useMemo(() => {
    const built = buildUniqueLocationRows(
      data.locations,
      activityFilter,
      platformFilter,
      firstVisitSince,
    );
    return sortUniqueLocationRows(built, sortKey, sortAsc);
  }, [activityFilter, data.locations, firstVisitSince, platformFilter, sortAsc, sortKey]);

  const filteredLocationCount = useMemo(() => {
    if (!firstVisitSince) {
      return uniqueLocationCountForActivity(data, activityFilter);
    }
    return buildUniqueLocationRows(data.locations, activityFilter, "all", firstVisitSince).length;
  }, [activityFilter, data, firstVisitSince]);

  const showVolunteerRolesHint = useMemo(
    () =>
      activityFilter !== "runs" &&
      rows.some((row) => volunteerRolesTooltipLines(row.location) !== null),
    [activityFilter, rows],
  );

  const uniqueLocationCount = filteredLocationCount;

  const activitySummary =
    activityFilter === "runs"
      ? locationsWithRunsSummary(uniqueLocationCount)
      : activityFilter === "volunteering"
        ? locationsWithVolunteeringSummary(uniqueLocationCount)
        : uniqueLocationsLabel(uniqueLocationCount);

  return (
    <>
      <div className="unique-locations-summary muted">
        {activitySummary}
        {platformFilter !== "all" && ` · ${platformLabel(platformFilter)}`}
        {data.unmapped_locations > 0 && ` · без координат: ${data.unmapped_locations}`}
      </div>

      <div className="unique-locations-filters" role="tablist" aria-label="Фильтр по системам">
        <button
          type="button"
          role="tab"
          aria-selected={platformFilter === "all"}
          className={platformFilter === "all" ? "map-mode-tab active" : "map-mode-tab"}
          onClick={() => onPlatformFilterChange("all")}
        >
          Все ({uniqueLocationCount})
        </button>
        {platformCounts.map((item) => (
          <button
            key={item.platform_code}
            type="button"
            role="tab"
            aria-selected={platformFilter === item.platform_code}
            className={platformFilter === item.platform_code ? "map-mode-tab active" : "map-mode-tab"}
            onClick={() => onPlatformFilterChange(item.platform_code)}
          >
            {platformLabel(item.platform_code)} ({item.location_count})
          </button>
        ))}
      </div>


      {rows.length === 0 ? (
        <p className="muted unique-locations-empty">Нет локаций для выбранного фильтра.</p>
      ) : (
        <div className="unique-locations-table-wrap">
          <table className="data-table unique-locations-table">
            <thead>
              <tr>
                <ColumnHeader
                  label="Локация"
                  filterable={false}
                  sortActive={sortKey === "name"}
                  sortAsc={sortAsc}
                  onSort={() => handleSort("name")}
                />
                <ColumnHeader
                  label="Город"
                  filterable={false}
                  sortActive={sortKey === "city"}
                  sortAsc={sortAsc}
                  onSort={() => handleSort("city")}
                />
                <PlainColumnHeader label="Системы" />
                <ColumnHeader
                  label={firstVisitColumnLabel(activityFilter)}
                  filterable={false}
                  sortActive={sortKey === "first_visit"}
                  sortAsc={sortAsc}
                  onSort={() => handleSort("first_visit")}
                />
                <ColumnHeader
                  label="Визитов"
                  filterable={false}
                  sortActive={sortKey === "visit_count"}
                  sortAsc={sortAsc}
                  onSort={() => handleSort("visit_count")}
                />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const first = firstDate(row.dates);
                return (
                  <tr key={row.location.catalog_identity_key}>
                    <td className="col-location">
                      <span className="unique-locations-name">
                        <LocationNameLink name={row.location.name} slug={row.location.location_slug} />
                      </span>
                      {activityFilter !== "volunteering" && !row.location.has_coordinates && (
                        <span className="unique-locations-badge-muted">нет на карте</span>
                      )}
                      {activityFilter !== "volunteering" &&
                        row.location.has_coordinates &&
                        row.location.is_cancelled && (
                        <span className="unique-locations-badge-cancelled">отменена</span>
                      )}
                      {activityFilter !== "volunteering" &&
                        row.location.has_coordinates &&
                        !row.location.is_cancelled &&
                        row.location.is_paused && (
                        <span className="unique-locations-badge-paused">на паузе</span>
                      )}
                    </td>
                    <td className="col-city muted">{row.location.city ?? "—"}</td>
                    <td className="col-platform">
                      <div className="unique-locations-platform-badges">
                        {row.activePlatforms.map((platform) => (
                          <PlatformBadge key={platform.platform_code} code={platform.platform_code} />
                        ))}
                      </div>
                    </td>
                    <td className="col-date">{first ? formatDate(first) : "—"}</td>
                    <td className="col-count">
                      <VisitCountCell row={row} activityFilter={activityFilter} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {showVolunteerRolesHint && (
            <p className="muted unique-locations-table-hint">
              Наведите на число визитов, чтобы увидеть роли волонтёра на этой локации.
            </p>
          )}
        </div>
      )}
    </>
  );
}

export function UniqueLocationsModal({
  open,
  onClose,
  includeTest = false,
  activityFilter = "all",
  firstVisitSince,
  fetchDetail,
}: UniqueLocationsModalProps) {
  const { getUniqueLocationsDetail } = useAppDataSource();
  const [data, setData] = useState<UniqueLocationsDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [platformFilter, setPlatformFilter] = useState<PlatformFilter>("all");

  useEffect(() => {
    if (!open) {
      return;
    }
    setPlatformFilter("all");
    setLoading(true);
    setError(null);
    const loadDetail = fetchDetail ?? getUniqueLocationsDetail;
    void loadDetail(includeTest)
      .then((response) => setData(response))
      .catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить список"))
      .finally(() => setLoading(false));
  }, [open, includeTest, fetchDetail, getUniqueLocationsDetail]);

  return (
    <DetailModal
      open={open}
      title={modalTitleForActivity(activityFilter, firstVisitSince)}
      onClose={onClose}
    >
      {loading && <p className="muted">Загрузка…</p>}
      {error && <p className="error-text">{error}</p>}
      {!loading && !error && data && (
        <ModalContent
          data={data}
          platformFilter={platformFilter}
          activityFilter={activityFilter}
          firstVisitSince={firstVisitSince}
          onPlatformFilterChange={setPlatformFilter}
        />
      )}
    </DetailModal>
  );
}
