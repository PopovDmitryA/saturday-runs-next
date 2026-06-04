import { formatDate } from "../lib/format";

type ActivityDateLinkProps = {
  date: string;
  url?: string | null;
  className?: string;
};

export function ActivityDateLink({ date, url, className }: ActivityDateLinkProps) {
  const formatted = formatDate(date);
  if (!url) {
    return <span className={className}>{formatted}</span>;
  }
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className={className ? `activity-date-link ${className}` : "activity-date-link"}
      title="Открыть на сайте платформы"
    >
      {formatted}
    </a>
  );
}
