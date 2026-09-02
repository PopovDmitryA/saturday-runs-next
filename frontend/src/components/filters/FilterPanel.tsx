import type { ChangeEvent, ReactNode } from "react";
import "./filters.css";

/**
 * Единая панель фильтров для всех витрин сайта.
 *
 * Образец — панель рейтингов (решение Дмитрия 28.08.2026: «блок с фильтрами по
 * всем дашбордам сделать такой же, с обрамлением, как в рейтинге уникальных
 * локаций»). До этого каждая витрина городила свой набор: где-то чипы без
 * подписей, где-то тулбар без рамки, а поле поиска отличалось и размером
 * (padding 0.75rem против 0.45rem), и шириной (320px / 420px / без предела).
 *
 * Состав:
 * - FilterPanel — рамка вокруг всех фильтров страницы;
 * - FilterRow — ряд групп внутри панели (переносится на узких экранах);
 * - FilterGroup — подпись + контрол;
 * - FilterTabs — сегментированный переключатель (таблетки);
 * - FilterSearch — поле поиска единого размера, всегда последнее в панели.
 */

export function FilterPanel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={`fp-panel${className ? ` ${className}` : ""}`}>{children}</div>;
}

export function FilterRow({
  children,
  /** Отделить ряд линией сверху — для второй строки фильтров. */
  divided = false,
  className = "",
}: {
  children: ReactNode;
  divided?: boolean;
  className?: string;
}) {
  return (
    <div className={`fp-row${divided ? " fp-row-divided" : ""}${className ? ` ${className}` : ""}`}>
      {children}
    </div>
  );
}

export function FilterGroup({
  label,
  hint,
  children,
  /** Прижать группу к правому краю ряда (обычно так стоит поиск). */
  trailing = false,
}: {
  label?: ReactNode;
  hint?: ReactNode;
  children: ReactNode;
  trailing?: boolean;
}) {
  return (
    <div className={`fp-group${trailing ? " fp-group-trailing" : ""}`}>
      {label && (
        <span className="fp-label">
          {label}
          {hint}
        </span>
      )}
      {children}
    </div>
  );
}

export type FilterTabOption<T extends string | number> = {
  value: T;
  label: ReactNode;
  /** Подсказка на кнопке (title). */
  title?: string;
};

export function FilterTabs<T extends string | number>({
  options,
  value,
  onChange,
  ariaLabel,
  /** role="tablist" вместо group — когда переключатель меняет вид, а не фильтрует. */
  asTablist = false,
}: {
  options: readonly FilterTabOption<T>[];
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
  asTablist?: boolean;
}) {
  return (
    <div className="fp-tabs" role={asTablist ? "tablist" : "group"} aria-label={ariaLabel}>
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={String(option.value)}
            type="button"
            role={asTablist ? "tab" : undefined}
            aria-selected={asTablist ? active : undefined}
            aria-pressed={asTablist ? undefined : active}
            title={option.title}
            className={`fp-tab${active ? " fp-tab-active" : ""}`}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/**
 * Выпадающий список для фильтров с длинным перечнем значений.
 *
 * Ряд кнопок хорош, пока значений 3-5; годы площадки (2015…2026) и недели
 * протокола (сотни) в него не помещаются и уезжают за край экрана — там нужен
 * именно select (просьба Дмитрия 02.09.2026).
 */
export function FilterSelect<T extends string | number>({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: readonly FilterTabOption<T>[];
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
}) {
  return (
    <select
      className="fp-select"
      aria-label={ariaLabel}
      value={String(value)}
      onChange={(event) => {
        const picked = options.find((option) => String(option.value) === event.target.value);
        if (picked) {
          onChange(picked.value);
        }
      }}
    >
      {options.map((option) => (
        <option key={String(option.value)} value={String(option.value)} title={option.title}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

export function FilterSearch({
  value,
  onChange,
  placeholder = "Поиск по имени…",
  ariaLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  ariaLabel?: string;
}) {
  return (
    <input
      className="fp-search"
      type="search"
      placeholder={placeholder}
      aria-label={ariaLabel ?? placeholder}
      value={value}
      onChange={(event: ChangeEvent<HTMLInputElement>) => onChange(event.target.value)}
    />
  );
}
