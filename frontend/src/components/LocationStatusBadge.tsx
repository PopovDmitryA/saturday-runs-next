type LocationStatusBadgeProps = {
  isPaused?: boolean;
  isCancelled?: boolean;
  isUpcoming?: boolean;
};

/**
 * Три состояния площадки и отдельно отмена ближайшего старта.
 *
 * Порядок важен: «не действует» сильнее отмены. Если площадка вообще не
 * работает, сообщать про отменённую субботу бессмысленно; а вот у работающей
 * отмена — главное, что нужно знать перед выездом.
 */
export function LocationStatusBadge({
  isPaused,
  isCancelled,
  isUpcoming,
}: LocationStatusBadgeProps) {
  if (isPaused) {
    return <span className="location-status-badge location-status-paused">не действует</span>;
  }
  if (isCancelled) {
    return <span className="location-status-badge location-status-cancelled">отмена старта</span>;
  }
  if (isUpcoming) {
    return <span className="location-status-badge location-status-upcoming">скоро</span>;
  }
  return null;
}

export function LocationStatusLabel({
  isPaused,
  isCancelled,
  isUpcoming,
}: LocationStatusBadgeProps) {
  if (isPaused) {
    return <span className="location-status-label location-status-paused">Не действует</span>;
  }
  if (isCancelled) {
    return <span className="location-status-label location-status-cancelled">Отмена старта</span>;
  }
  if (isUpcoming) {
    return <span className="location-status-label location-status-upcoming">Скоро</span>;
  }
  return <span className="location-status-label location-status-active">Активна</span>;
}
