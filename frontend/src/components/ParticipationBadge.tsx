import type { ParticipationType } from "../lib/api";

type ParticipationBadgeProps = {
  type: ParticipationType;
};

const LABEL: Record<ParticipationType, string> = {
  run: "Пробежка",
  volunteer: "Волонтёр",
};

export function ParticipationBadge({ type }: ParticipationBadgeProps) {
  return (
    <span className={`badge badge-participation badge-participation-${type}`}>
      {LABEL[type]}
    </span>
  );
}
