type ActivityTableColsProps = {
  variant: "runs" | "volunteering";
  /** Доп. узкая колонка со звездой-оценкой (только свой раздел пробежек). */
  withRating?: boolean;
};

export function ActivityTableCols({ variant, withRating = false }: ActivityTableColsProps) {
  if (variant === "volunteering") {
    return (
      <colgroup>
        <col className="col-date" />
        <col className="col-platform" />
        <col className="col-location" />
        <col className="col-role" />
        {withRating && <col className="col-rating" />}
      </colgroup>
    );
  }

  return (
    <colgroup>
      <col className="col-date" />
      <col className="col-platform" />
      <col className="col-location" />
      <col className="col-compact" />
      <col className="col-time" />
      <col className="col-pace" />
      {/* «Темп» без своего col тянется на остаток; при рейтинге держим это
          пустым col, а последним ставим узкий col-rating. */}
      {withRating && <col />}
      {withRating && <col className="col-rating" />}
    </colgroup>
  );
}
