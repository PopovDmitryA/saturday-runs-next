type ActivityTableColsProps = {
  variant: "runs" | "volunteering";
};

export function ActivityTableCols({ variant }: ActivityTableColsProps) {
  if (variant === "volunteering") {
    return (
      <colgroup>
        <col className="col-date" />
        <col className="col-platform" />
        <col className="col-location" />
        <col className="col-role" />
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
    </colgroup>
  );
}
