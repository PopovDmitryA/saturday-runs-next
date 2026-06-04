type FilterIconProps = {
  className?: string;
};

export function FilterIcon({ className }: FilterIconProps) {
  return (
    <svg
      className={className ?? "filter-icon"}
      viewBox="0 0 16 16"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M1.5 2.5h13L9.2 8.4v4.1L6.8 13V8.4L1.5 2.5z" fill="currentColor" />
    </svg>
  );
}
