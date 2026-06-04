import { useCallback, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { FilterIcon } from "./FilterIcon";
import { FilterPopover } from "./FilterPopover";

type ColumnHeaderProps = {
  label: string;
  filterable?: boolean;
  filterActive?: boolean;
  sortActive?: boolean;
  sortAsc?: boolean;
  onSort?: () => void;
  filterTitle?: string;
  filterContent?: ReactNode;
  filterFooter?: ReactNode;
};

function ColumnHeaderText({ label }: { label: string }) {
  const textRef = useRef<HTMLSpanElement>(null);
  const [title, setTitle] = useState<string | undefined>(undefined);

  const updateTitle = useCallback(() => {
    const element = textRef.current;
    if (!element) {
      return;
    }
    setTitle(element.scrollWidth > element.clientWidth ? label : undefined);
  }, [label]);

  useLayoutEffect(() => {
    updateTitle();
    const element = textRef.current;
    if (!element) {
      return;
    }
    const observer = new ResizeObserver(updateTitle);
    observer.observe(element);
    return () => observer.disconnect();
  }, [updateTitle]);

  return (
    <span ref={textRef} className="th-col-text" title={title}>
      {label}
    </span>
  );
}

export function ColumnHeader({
  label,
  filterable = true,
  filterActive = false,
  sortActive = false,
  sortAsc = false,
  onSort,
  filterTitle,
  filterContent,
  filterFooter,
}: ColumnHeaderProps) {
  const [open, setOpen] = useState(false);
  const filterButtonRef = useRef<HTMLButtonElement>(null);
  const showFilter = filterable && filterContent;

  return (
    <th className="th-col">
      <div className="th-col-inner">
        {onSort ? (
          <button
            type="button"
            className={`th-col-label${sortActive ? " th-col-label-active" : ""}`}
            onClick={onSort}
          >
            <ColumnHeaderText label={label} />
            <span className="th-col-sort">{sortActive ? (sortAsc ? "↑" : "↓") : "↕"}</span>
          </button>
        ) : (
          <span className="th-col-label th-col-label-static">
            <ColumnHeaderText label={label} />
          </span>
        )}
        {showFilter && (
          <button
            ref={filterButtonRef}
            type="button"
            className={`th-filter-trigger${filterActive ? " active" : ""}${open ? " open" : ""}`}
            aria-label={`Фильтр: ${label}`}
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
          >
            <FilterIcon />
          </button>
        )}
      </div>
      {showFilter && (
        <FilterPopover
          open={open}
          anchorEl={filterButtonRef.current}
          title={filterTitle ?? label}
          onClose={() => setOpen(false)}
          footer={filterFooter}
        >
          {filterContent}
        </FilterPopover>
      )}
    </th>
  );
}

export function PlainColumnHeader({ label }: { label: string }) {
  return (
    <th className="th-col">
      <span className="th-col-label th-col-label-static">
        <ColumnHeaderText label={label} />
      </span>
    </th>
  );
}
