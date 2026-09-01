import { FilterGroup } from "./FilterPanel";
import "./filters.css";

/**
 * Единый фильтр «Система» на весь сайт (решение Дмитрия 01.09.2026).
 *
 * До этого он выглядел по-разному на каждой витрине: в каталоге и последних
 * пробежках — цветные бейджи-чипы, в едином протоколе и журнале локации —
 * «map-mode» кнопки, в рейтингах — таблетки, на хабе рейтингов — свои. И вёл
 * себя по-разному: где-то выбор одной системы, где-то повторный клик снимал
 * фильтр, а выбрать сразу две было нельзя нигде.
 *
 * Теперь вид один, а поведение зависит от смысла фильтра:
 * - mode="multi" — фильтр СТРОК (каталог, результаты, журналы, кабинет):
 *   систем можно отметить сколько угодно, «Все» снимает отбор;
 * - mode="single" — ЗАЧЁТ (рейтинги, единый протокол): внутри выбранной
 *   системы пересчитываются места, поэтому выбор ровно один.
 */

export type PlatformOption = {
  code: string;
  label: string;
  /** Число рядом с подписью («5 вёрст (12)») — необязательно. */
  count?: number | null;
  disabled?: boolean;
  title?: string;
};

type CommonProps = {
  options: readonly PlatformOption[];
  /** Подпись группы; передайте null, чтобы вставить фильтр в свою вёрстку. */
  label?: string | null;
  hint?: React.ReactNode;
  allLabel?: string;
  ariaLabel?: string;
};

type MultiProps = CommonProps & {
  mode: "multi";
  /** Пустое множество = «Все». */
  value: ReadonlySet<string>;
  onChange: (next: Set<string>) => void;
};

type SingleProps = CommonProps & {
  mode: "single";
  /** "all" или код системы. */
  value: string;
  onChange: (next: string) => void;
};

export function PlatformFilter(props: MultiProps | SingleProps) {
  const { options, label = "Система", hint, allLabel = "Все", ariaLabel = "Система" } = props;
  const allActive = props.mode === "multi" ? props.value.size === 0 : props.value === "all";

  const toggle = (code: string) => {
    if (props.mode === "single") {
      // Повторный клик по выбранной системе возвращает общий зачёт: иначе из
      // него нельзя выйти, не целясь в кнопку «Все».
      props.onChange(props.value === code ? "all" : code);
      return;
    }
    const next = new Set(props.value);
    if (next.has(code)) {
      next.delete(code);
    } else {
      next.add(code);
    }
    props.onChange(next);
  };

  const control = (
    <div className="fp-tabs" role="group" aria-label={ariaLabel}>
      <button
        type="button"
        aria-pressed={allActive}
        className={`fp-tab${allActive ? " fp-tab-active" : ""}`}
        onClick={() => (props.mode === "multi" ? props.onChange(new Set()) : props.onChange("all"))}
      >
        {allLabel}
      </button>
      {options.map((option) => {
        const active =
          props.mode === "multi" ? props.value.has(option.code) : props.value === option.code;
        return (
          <button
            key={option.code}
            type="button"
            aria-pressed={active}
            disabled={option.disabled}
            title={option.title}
            className={`fp-tab${active ? " fp-tab-active" : ""}`}
            onClick={() => toggle(option.code)}
          >
            {option.label}
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
