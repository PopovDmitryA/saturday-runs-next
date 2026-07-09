/** Кнопка разворота карты на весь экран (общая для карт локаций и регионов). */
export function MapFullscreenButton({
  isFullscreen,
  onToggle,
}: {
  isFullscreen: boolean;
  onToggle: () => void;
}) {
  const label = isFullscreen ? "Свернуть карту" : "Развернуть карту на весь экран";
  return (
    <button
      type="button"
      className="location-map-fullscreen-btn"
      onClick={onToggle}
      title={label}
      aria-label={label}
    >
      {isFullscreen ? (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <polyline points="4 14 10 14 10 20" />
          <polyline points="20 10 14 10 14 4" />
          <line x1="10" y1="14" x2="3" y2="21" />
          <line x1="21" y1="3" x2="14" y2="10" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <polyline points="15 3 21 3 21 9" />
          <polyline points="9 21 3 21 3 15" />
          <line x1="21" y1="3" x2="14" y2="10" />
          <line x1="3" y1="21" x2="10" y2="14" />
        </svg>
      )}
    </button>
  );
}
