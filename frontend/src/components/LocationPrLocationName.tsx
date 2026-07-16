import { type ReactNode } from "react";
import { StatHintTooltip } from "./StatHintTooltip";

const LOCATION_PR_TOOLTIP =
  "Рекорд локации — лучший результат среди всех ваших пробежек на этой площадке, во всех системах.";

type LocationPrLocationNameProps = {
  isLocationPr: boolean;
  children: ReactNode;
};

export function LocationPrLocationName({ isLocationPr, children }: LocationPrLocationNameProps) {
  if (!isLocationPr) {
    return <>{children}</>;
  }

  return (
    <StatHintTooltip text={LOCATION_PR_TOOLTIP} className="location-name-location-pr-tooltip">
      <span className="location-name-location-pr">{children}</span>
    </StatHintTooltip>
  );
}
