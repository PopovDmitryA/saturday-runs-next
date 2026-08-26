import { formatDate } from "../lib/format";
import { ourProtocolHref, type ProtocolTarget } from "../lib/protocolHref";

type ActivityDateLinkProps = {
  date: string;
  /**
   * Старт, чей протокол открывать у нас. Если он известен — ведём внутрь
   * сайта; `url` остаётся запасным вариантом (тестовые старты, локация без
   * разобранного слага).
   */
  target?: ProtocolTarget | null;
  url?: string | null;
  className?: string;
};

export function ActivityDateLink({ date, target, url, className }: ActivityDateLinkProps) {
  const formatted = formatDate(date);
  const internal = target ? ourProtocolHref(target) : null;
  const href = internal ?? url;
  if (!href) {
    return <span className={className}>{formatted}</span>;
  }
  const classes = className ? `activity-date-link ${className}` : "activity-date-link";
  if (internal) {
    return (
      <a href={internal} className={classes} title="Открыть протокол старта">
        {formatted}
      </a>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className={classes}
      title="Открыть на сайте платформы"
    >
      {formatted}
    </a>
  );
}
