import { useEffect, useRef, useState } from "react";
import type React from "react";
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
 * протокола (сотни) в него не помещаются. Родной `select` тоже не годится:
 * системное меню раскрывается на всю высоту экрана, накрывает саму кнопку и
 * расползается в обе стороны от неё (Дмитрий 02.09.2026). Поэтому свой
 * listbox: высота ограничена долей экрана, открывается ПОД полем и только
 * вниз, а вверх — лишь когда снизу места нет. Клавиатура — как у родного:
 * стрелки ходят по пунктам, Enter выбирает, Esc закрывает.
 */
export function FilterSelect<T extends string | number>({
  options,
  value,
  onChange,
  ariaLabel,
  disabled = false,
  title,
}: {
  options: readonly FilterTabOption<T>[];
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
  disabled?: boolean;
  /** Подсказка на кнопке — например, почему список выключен. */
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  const [dropUp, setDropUp] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const selected = options.find((option) => option.value === value);

  // Клик мимо и Esc закрывают список; Esc возвращает фокус на кнопку.
  useEffect(() => {
    if (!open) {
      return;
    }
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        rootRef.current?.querySelector("button")?.focus();
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  // Куда раскрыться, подвести выбранное к глазам и отдать ему фокус.
  useEffect(() => {
    if (!open) {
      return;
    }
    const button = rootRef.current?.querySelector("button");
    if (button) {
      const box = button.getBoundingClientRect();
      // Ниже кнопки места меньше, чем выше, — открываемся вверх.
      setDropUp(window.innerHeight - box.bottom < box.top && window.innerHeight - box.bottom < 220);
    }
    const current =
      listRef.current?.querySelector<HTMLElement>('[aria-selected="true"]') ??
      listRef.current?.querySelector<HTMLElement>('[role="option"]');
    current?.scrollIntoView({ block: "nearest" });
    current?.focus({ preventScroll: true });
  }, [open]);

  // Стрелки внутри списка ходят по пунктам, Home/End — в края.
  const onListKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(
      listRef.current?.querySelectorAll<HTMLElement>('[role="option"]') ?? [],
    );
    if (items.length === 0) {
      return;
    }
    const index = items.indexOf(document.activeElement as HTMLElement);
    let next = -1;
    if (event.key === "ArrowDown") {
      next = Math.min(items.length - 1, index + 1);
    } else if (event.key === "ArrowUp") {
      next = Math.max(0, index - 1);
    } else if (event.key === "Home") {
      next = 0;
    } else if (event.key === "End") {
      next = items.length - 1;
    } else if (event.key === "Tab") {
      setOpen(false);
      return;
    }
    if (next >= 0) {
      event.preventDefault();
      items[next].focus();
      items[next].scrollIntoView({ block: "nearest" });
    }
  };

  return (
    <div className="fp-select" ref={rootRef}>
      <button
        type="button"
        className="fp-select-button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        title={title}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={(event) => {
          // Стрелка вниз на закрытой кнопке — открыть, как у родного select.
          if (!open && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
            event.preventDefault();
            setOpen(true);
          }
        }}
      >
        <span className="fp-select-value">{selected?.label ?? String(value)}</span>
        <span className="fp-select-caret" aria-hidden="true" />
      </button>
      {open && (
        <div
          className={`fp-select-list${dropUp ? " fp-select-list-up" : ""}`}
          role="listbox"
          aria-label={ariaLabel}
          ref={listRef}
          onKeyDown={onListKeyDown}
        >
          {options.map((option) => {
            const active = option.value === value;
            return (
              <button
                key={String(option.value)}
                type="button"
                role="option"
                aria-selected={active}
                title={option.title}
                tabIndex={-1}
                className={`fp-select-option${active ? " fp-select-option-active" : ""}`}
                onClick={() => {
                  onChange(option.value);
                  setOpen(false);
                  rootRef.current?.querySelector("button")?.focus();
                }}
              >
                {option.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
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
