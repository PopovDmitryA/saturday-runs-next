import type { DashboardAnalytics } from "../lib/api";
import { PlatformBadge } from "./PlatformBadge";
import { StatHintTooltip } from "./StatHintTooltip";

const TOP_LOCATION_TIE_HINT =
  "У участника несколько локаций с одинаковым количеством посещений";

type TopLocationValueProps = {
  topLocation: NonNullable<DashboardAnalytics["top_location"]>;
};

export function TopLocationValue({ topLocation }: TopLocationValueProps) {
  const hasTiedLocations = (topLocation.tied_count ?? 1) > 1;

  return (
    <span className="stat-value-location">
      <span>{topLocation.name}</span>
      {hasTiedLocations && (
        <StatHintTooltip text={TOP_LOCATION_TIE_HINT} className="top-location-tie-star">
          <span aria-hidden="true">★</span>
        </StatHintTooltip>
      )}
      <PlatformBadge code={topLocation.platform_code} />
    </span>
  );
}
