import { useState, type MouseEvent } from "react";
import { formatInt } from "../lib/format";

type DashboardStatCardProps = {
  href?: string;
  value: number;
  label: string;
  variant: "runs" | "volunteering";
};

export function DashboardStatCard({ href, value, label, variant }: DashboardStatCardProps) {
  const [animating, setAnimating] = useState(false);
  const className = `stat-card stat-card-${variant}${href ? " stat-card-nav" : ""}${animating ? " stat-card-nav-animate" : ""}`;

  if (!href) {
    return (
      <div className={className}>
        <span className="stat-value">{formatInt(value)}</span>
        <span className="stat-label">{label}</span>
      </div>
    );
  }

  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault();
    if (animating) {
      return;
    }
    setAnimating(true);
    window.setTimeout(() => {
      window.location.href = href;
    }, 180);
  };

  return (
    <a
      href={href}
      className={`stat-card stat-card-nav stat-card-${variant}${animating ? " stat-card-nav-animate" : ""}`}
      onClick={handleClick}
    >
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </a>
  );
}
