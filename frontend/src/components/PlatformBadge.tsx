import { platformCodeLabel } from "../lib/format";

type PlatformBadgeProps = {
  code: string;
};

const PLATFORM_VARIANT: Record<string, string> = {
  five_verst: "badge-platform-five-verst",
  s95: "badge-platform-s95",
  parkrun: "badge-platform-parkrun",
  runpark: "badge-platform-runpark",
};

export function PlatformBadge({ code }: PlatformBadgeProps) {
  const variant = PLATFORM_VARIANT[code] ?? "badge-platform-default";
  return (
    <span className={`badge badge-platform ${variant}`}>{platformCodeLabel(code)}</span>
  );
}
