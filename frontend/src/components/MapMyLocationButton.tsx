/**
 * Кнопка «где я» на карте. Первое нажатие показывает окно браузера с вопросом
 * о геопозиции — своего диалога-предупреждения не рисуем, он бы только удваивал
 * штатный.
 */
export function MapMyLocationButton({
  state,
  onClick,
}: {
  /** idle — не искали, loading — ждём GPS, ready — точка на карте, denied — отказ. */
  state: "idle" | "loading" | "ready" | "denied";
  onClick: () => void;
}) {
  const label =
    state === "denied"
      ? "Доступ к геопозиции запрещён — включите его в настройках браузера"
      : state === "loading"
        ? "Определяю, где вы…"
        : state === "ready"
          ? "Вернуться к своему положению"
          : "Показать, где я, и приблизить к ближайшим стартам";
  return (
    <button
      type="button"
      className={`location-map-locate-btn${
        state === "ready" ? " location-map-locate-btn-active" : ""
      }${state === "loading" ? " location-map-locate-btn-loading" : ""}`}
      onClick={onClick}
      disabled={state === "loading"}
      title={label}
      aria-label={label}
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <circle cx="12" cy="12" r="3" />
        <line x1="12" y1="2" x2="12" y2="5" />
        <line x1="12" y1="19" x2="12" y2="22" />
        <line x1="2" y1="12" x2="5" y2="12" />
        <line x1="19" y1="12" x2="22" y2="12" />
        <circle cx="12" cy="12" r="8" />
      </svg>
    </button>
  );
}
