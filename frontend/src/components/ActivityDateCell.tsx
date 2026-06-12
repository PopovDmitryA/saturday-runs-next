import type { ReactNode } from "react";

type ActivityDateCellProps = {
  date: ReactNode;
  badges?: ReactNode;
};

export function ActivityDateCell({ date, badges }: ActivityDateCellProps) {
  return (
    <div className="activity-date-cell">
      <span className="activity-date-value">{date}</span>
      <span className="activity-date-badges">{badges}</span>
    </div>
  );
}
