import { useMemo, useState } from "react";

export type CheckboxFilterOption = {
  value: string;
  label: string;
};

type CheckboxListFilterProps = {
  options: CheckboxFilterOption[];
  selected: Set<string>;
  onSelectedChange: (selected: Set<string>) => void;
  searchPlaceholder?: string;
};

export function CheckboxListFilter({
  options,
  selected,
  onSelectedChange,
  searchPlaceholder = "Поиск…",
}: CheckboxListFilterProps) {
  const [search, setSearch] = useState("");

  const filteredOptions = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) {
      return options;
    }
    return options.filter((option) => option.label.toLowerCase().includes(query));
  }, [options, search]);

  const allValues = useMemo(() => options.map((option) => option.value), [options]);
  const allSelected = selected.size === allValues.length;

  const toggleValue = (value: string, checked: boolean) => {
    const next = new Set(selected);
    if (checked) {
      next.add(value);
    } else {
      next.delete(value);
    }
    onSelectedChange(next);
  };

  const selectAll = () => onSelectedChange(new Set(allValues));
  const clearAll = () => onSelectedChange(new Set());

  return (
    <div className="checkbox-filter">
      {options.length > 6 && (
        <input
          className="filter-popover-search"
          type="search"
          value={search}
          placeholder={searchPlaceholder}
          onChange={(event) => setSearch(event.target.value)}
        />
      )}
      <div className="checkbox-filter-actions">
        <button type="button" className="filter-popover-link" onClick={selectAll}>
          Все
        </button>
        <button type="button" className="filter-popover-link" onClick={clearAll}>
          Снять
        </button>
      </div>
      <ul className="checkbox-filter-list">
        {filteredOptions.length === 0 ? (
          <li className="muted checkbox-filter-empty">Ничего не найдено</li>
        ) : (
          filteredOptions.map((option) => (
            <li key={option.value}>
              <label className="checkbox-filter-row">
                <input
                  type="checkbox"
                  checked={selected.has(option.value)}
                  onChange={(event) => toggleValue(option.value, event.target.checked)}
                />
                <span className="checkbox-filter-label">{option.label}</span>
              </label>
            </li>
          ))
        )}
      </ul>
      {!allSelected && selected.size > 0 && (
        <p className="checkbox-filter-hint muted">Выбрано: {selected.size}</p>
      )}
    </div>
  );
}
