import type { MyRating } from "../lib/api";

type RunRatingStarProps = {
  rating: MyRating | undefined;
  canCreate: boolean;
  canRate: boolean;
  onOpen: () => void;
  /** Подсказка для колонки, где оценить нельзя (напр. окно 30 дней истекло). */
  naTitle?: string;
};

export function RunRatingStar({
  rating,
  canCreate,
  canRate,
  onOpen,
  naTitle = "Оценить можно только старты за последние 30 дней",
}: RunRatingStarProps) {
  // Звезда только если старт оценён (любой давности) или его ещё можно оценить.
  if (!rating && !canCreate) {
    return (
      <span className="run-rate-na" title={naTitle}>
        —
      </span>
    );
  }
  const rated = rating != null;
  const frozen = rated && !rating.editable;
  return (
    <button
      type="button"
      className={`run-rate-star ${rated ? "rated" : ""}`}
      disabled={!canRate}
      title={
        frozen
          ? `Оценка зафиксирована (старту > 3 мес): ${rating.score_overall}★`
          : rated
            ? `Ваша оценка: ${rating.score_overall}★ — изменить`
            : "Оценить старт"
      }
      aria-label={rated ? (frozen ? "Посмотреть оценку" : "Изменить оценку старта") : "Оценить старт"}
      onClick={onOpen}
    >
      <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
        <path
          d="M12 2.5l2.9 6.06 6.6.86-4.85 4.55 1.24 6.53L12 17.9l-5.89 3.06 1.24-6.53L2.5 9.42l6.6-.86L12 2.5z"
          fill={rated ? "currentColor" : "none"}
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
}
