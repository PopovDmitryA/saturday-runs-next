import { useState } from "react";

type StarRatingProps = {
  value: number; // 0 — не выбрано
  onChange: (value: number) => void;
  /** Разрешить сброс повторным кликом по той же звезде (для необязательных). */
  allowClear?: boolean;
  /** Только для чтения (зафиксированная оценка). */
  disabled?: boolean;
  ariaLabel?: string;
};

const STARS = [1, 2, 3, 4, 5];

export function StarRating({
  value,
  onChange,
  allowClear = false,
  disabled = false,
  ariaLabel,
}: StarRatingProps) {
  const [hover, setHover] = useState(0);
  const active = (disabled ? 0 : hover) || value;

  return (
    <div className={`star-rating ${disabled ? "disabled" : ""}`} role="radiogroup" aria-label={ariaLabel}>
      {STARS.map((star) => {
        const filled = star <= active;
        return (
          <button
            key={star}
            type="button"
            className={`star-btn ${filled ? "filled" : ""}`}
            role="radio"
            aria-checked={star === value}
            aria-label={`${star} из 5`}
            disabled={disabled}
            onMouseEnter={() => setHover(star)}
            onMouseLeave={() => setHover(0)}
            onFocus={() => setHover(star)}
            onBlur={() => setHover(0)}
            onClick={() => onChange(allowClear && value === star ? 0 : star)}
          >
            <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">
              <path
                d="M12 2.5l2.9 6.06 6.6.86-4.85 4.55 1.24 6.53L12 17.9l-5.89 3.06 1.24-6.53L2.5 9.42l6.6-.86L12 2.5z"
                fill={filled ? "currentColor" : "none"}
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        );
      })}
    </div>
  );
}
