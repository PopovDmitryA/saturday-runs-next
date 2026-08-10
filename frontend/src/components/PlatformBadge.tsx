import { platformCodeLabel } from "../lib/format";

type PlatformBadgeProps = {
  code: string;
  /** Если задан — бейдж становится ссылкой (открывается в новой вкладке). */
  href?: string | null;
  /** Действие внутри страницы: бейдж становится кнопкой (href при этом игнорируется). */
  onClick?: () => void;
  title?: string;
};

const PLATFORM_VARIANT: Record<string, string> = {
  five_verst: "badge-platform-five-verst",
  s95: "badge-platform-s95",
  parkrun: "badge-platform-parkrun",
  runpark: "badge-platform-runpark",
};

export function PlatformBadge({ code, href, onClick, title }: PlatformBadgeProps) {
  const variant = PLATFORM_VARIANT[code] ?? "badge-platform-default";
  if (onClick) {
    return (
      <button
        type="button"
        className={`badge badge-platform badge-platform-btn ${variant}`}
        onClick={onClick}
        title={title}
      >
        {platformCodeLabel(code)}
      </button>
    );
  }
  if (href) {
    return (
      <a
        className={`badge badge-platform badge-platform-link ${variant}`}
        href={href}
        target="_blank"
        rel="noreferrer"
        title={title}
      >
        {platformCodeLabel(code)}
        <span className="badge-platform-link-arrow" aria-hidden="true">↗</span>
      </a>
    );
  }
  return (
    <span className={`badge badge-platform ${variant}`}>{platformCodeLabel(code)}</span>
  );
}
