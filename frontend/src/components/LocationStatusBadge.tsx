type LocationStatusBadgeProps = {
  isPaused?: boolean;
  isCancelled?: boolean;
};

export function LocationStatusBadge({ isPaused, isCancelled }: LocationStatusBadgeProps) {
  if (isCancelled) {
    return <span className="location-status-badge location-status-cancelled">отменена</span>;
  }
  if (isPaused) {
    return <span className="location-status-badge location-status-paused">не действует</span>;
  }
  return null;
}

export function LocationStatusLabel({ isPaused, isCancelled }: LocationStatusBadgeProps) {
  if (isCancelled) {
    return <span className="location-status-label location-status-cancelled">Отменена</span>;
  }
  if (isPaused) {
    return <span className="location-status-label location-status-paused">Не действует</span>;
  }
  return <span className="location-status-label location-status-active">Активна</span>;
}
