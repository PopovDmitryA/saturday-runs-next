type ActivityTableColsProps = {
  variant: "runs" | "volunteering";
  /** Доп. узкая колонка со звездой-оценкой (только свой раздел пробежек). */
  withRating?: boolean;
  /** Краткий мобильный набор: Дата · Система · Локация · Место · Время. */
  short?: boolean;
  /** Колонка «Погода» (волонтёрства): значок и градусы в час старта. */
  withWeather?: boolean;
};

export function ActivityTableCols({ variant, withRating = false, short = false, withWeather = false }: ActivityTableColsProps) {
  if (variant === "runs" && short) {
    return (
      <colgroup>
        <col className="col-date" />
        <col className="col-platform" />
        <col className="col-location" />
        <col className="col-compact" />
        <col className="col-time" />
      </colgroup>
    );
  }
  if (variant === "volunteering") {
    return (
      <colgroup>
        <col className="col-date" />
        <col className="col-platform" />
        <col className="col-location" />
        {withWeather && <col className="col-weather" />}
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
