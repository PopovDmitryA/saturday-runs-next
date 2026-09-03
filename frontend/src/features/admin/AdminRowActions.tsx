import { useRef, useState } from "react";
import { FilterPopover } from "../../components/activityTable/FilterPopover";

export type RowAction = {
  key: string;
  label: string;
  /** Ссылка вместо кнопки: «Профиль» открывается в новой вкладке. */
  href?: string;
  onSelect?: () => void;
  title?: string;
};

/**
 * Одна кнопка «Действия» вместо столбика кнопок в строке таблицы.
 *
 * Четыре кнопки в ячейке переносились по ширине и растягивали строку втрое
 * (Дмитрий 03.09.2026) — теперь они живут в выпадающем списке, а строка
 * остаётся в одну высоту. Всплывашка та же, что у фильтров колонок: portal,
 * закрытие по Escape и клику мимо уже там.
 */
export function AdminRowActions({ actions, label = "Действия" }: { actions: RowAction[]; label?: string }) {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);

  if (actions.length === 0) {
    return null;
  }

  return (
    <>
      <button
        ref={anchorRef}
        type="button"
        className="btn btn-ghost btn-sm admin-row-actions-toggle"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {label} ▾
      </button>
      <FilterPopover
        open={open}
        anchorEl={anchorRef.current}
        onClose={() => setOpen(false)}
        title={label}
      >
        <div className="admin-row-actions-menu" role="menu">
          {actions.map((action) =>
            action.href ? (
              <a
                key={action.key}
                role="menuitem"
                className="admin-row-actions-item"
                href={action.href}
                target="_blank"
                rel="noreferrer"
                title={action.title}
                onClick={() => setOpen(false)}
              >
                {action.label}
              </a>
            ) : (
              <button
                key={action.key}
                type="button"
                role="menuitem"
                className="admin-row-actions-item"
                title={action.title}
                onClick={() => {
                  setOpen(false);
                  action.onSelect?.();
                }}
              >
                {action.label}
              </button>
            ),
          )}
        </div>
      </FilterPopover>
    </>
  );
}
