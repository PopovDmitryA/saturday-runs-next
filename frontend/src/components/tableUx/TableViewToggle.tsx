import { useState } from "react";

export type TableView = "short" | "full";

/**
 * Режим «Кратко | Полно». Всегда открывается кратким: это дефолт для телефона,
 * а запоминание выбора между заходами сбивало — страница неожиданно
 * открывалась широкой таблицей со скроллом.
 */
export function useTableView(_storageKey: string): [TableView, (view: TableView) => void] {
  return useState<TableView>("short");
}

type TableViewToggleProps = {
  value: TableView;
  onChange: (view: TableView) => void;
  /** Подпись полного режима — по умолчанию «Полно». */
  fullLabel?: string;
  /**
   * Дополнительный класс. `tview-toggle-always` показывает сегмент и на
   * компьютере: так сделано в рейтингах, где краткий вид убирает тяжёлые
   * колонки систем. По умолчанию сегмент виден только на узких экранах.
   */
  className?: string;
};

export function TableViewToggle({
  value,
  onChange,
  fullLabel = "Полно",
  className = "",
}: TableViewToggleProps) {
  return (
    <div
      className={`tview-toggle${className ? ` ${className}` : ""}`}
      role="group"
      aria-label="Набор колонок таблицы"
    >
      <button
        type="button"
        aria-pressed={value === "short"}
        className={`tview-tab${value === "short" ? " tview-tab-active" : ""}`}
        onClick={() => onChange("short")}
      >
        Кратко
      </button>
      <button
        type="button"
        aria-pressed={value === "full"}
        className={`tview-tab${value === "full" ? " tview-tab-active" : ""}`}
        onClick={() => onChange("full")}
      >
        {fullLabel}
      </button>
    </div>
  );
}
