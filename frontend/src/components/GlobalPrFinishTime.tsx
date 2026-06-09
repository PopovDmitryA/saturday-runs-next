import { type ReactNode } from "react";
import { StatHintTooltip } from "./StatHintTooltip";

const GLOBAL_PR_TOOLTIP =
  "Глобальный рекорд — лучший результат среди всех ваших пробежек во всех системах на момент этой даты.";

type GlobalPrFinishTimeProps = {
  isGlobalPr: boolean;
  children: ReactNode;
};

export function GlobalPrFinishTime({ isGlobalPr, children }: GlobalPrFinishTimeProps) {
  if (!isGlobalPr) {
    return <>{children}</>;
  }

  return (
    <StatHintTooltip text={GLOBAL_PR_TOOLTIP} className="finish-time-global-pr-tooltip">
      <span className="finish-time-global-pr">{children}</span>
    </StatHintTooltip>
  );
}
