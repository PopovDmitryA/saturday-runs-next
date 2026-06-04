import type { VisitedFilter } from "./catalogLocationsTableHelpers";

type VisitedFilterPopoverProps = {
  value: VisitedFilter;
  onChange: (value: VisitedFilter) => void;
};

const OPTIONS: Array<{ value: VisitedFilter; label: string }> = [
  { value: "all", label: "Все" },
  { value: "visited", label: "Посещал" },
  { value: "not_visited", label: "Не посещал" },
];

export function VisitedFilterPopover({ value, onChange }: VisitedFilterPopoverProps) {
  return (
    <ul className="radio-filter-list">
      {OPTIONS.map((option) => (
        <li key={option.value}>
          <label className="radio-filter-row">
            <input
              type="radio"
              name="catalog-visited-filter"
              checked={value === option.value}
              onChange={() => onChange(option.value)}
            />
            <span className="radio-filter-label">{option.label}</span>
          </label>
        </li>
      ))}
    </ul>
  );
}
