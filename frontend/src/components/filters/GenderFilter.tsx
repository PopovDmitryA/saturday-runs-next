import { FilterGroup } from "./FilterPanel";
import "./filters.css";

/**
 * Единый фильтр по полу: «Все | М | Ж» короткими цветными тегами (решение
 * Дмитрия 01.09.2026). Раньше на каждой витрине писалось полностью —
 * «Мужчины (3)», «Женщины (3)» — и три слова съедали половину ряда фильтров,
 * хотя М и Ж узнаются мгновенно. Цвета те же, что у гистограммы финишей:
 * мужчины синие, женщины розовые.
 */

export type GenderValue = "all" | "male" | "female";

export type GenderOption = {
  value: GenderValue;
  /** Число рядом с тегом («М 3») — необязательно. */
  count?: number | null;
  disabled?: boolean;
  title?: string;
};

const SHORT_LABELS: Record<GenderValue, string> = {
  all: "Все",
  male: "М",
  female: "Ж",
};

const FULL_LABELS: Record<GenderValue, string> = {
  all: "Все",
  male: "Мужчины",
  female: "Женщины",
};

export function GenderFilter({
  options,
  value,
  onChange,
  label = "Пол",
  hint,
  /** Подпись «Все» иногда значит «абсолютный зачёт» — у рейтингов побед. */
  allLabel,
}: {
  options: readonly GenderOption[];
  value: GenderValue;
  onChange: (next: GenderValue) => void;
  label?: string | null;
  hint?: React.ReactNode;
  allLabel?: string;
}) {
  const control = (
    <div className="fp-tabs fp-gender" role="group" aria-label="Пол">
      {options.map((option) => {
        const active = option.value === value;
        const short = option.value === "all" && allLabel ? allLabel : SHORT_LABELS[option.value];
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            aria-label={FULL_LABELS[option.value]}
            disabled={option.disabled}
            title={option.title ?? FULL_LABELS[option.value]}
            className={`fp-tab fp-tab-${option.value}${active ? " fp-tab-active" : ""}`}
            onClick={() => onChange(option.value)}
          >
            {short}
            {option.count != null && <span className="fp-tab-count">{option.count}</span>}
          </button>
        );
      })}
    </div>
  );

  if (label === null) {
    return control;
  }
  return (
    <FilterGroup label={label} hint={hint}>
      {control}
    </FilterGroup>
  );
}
