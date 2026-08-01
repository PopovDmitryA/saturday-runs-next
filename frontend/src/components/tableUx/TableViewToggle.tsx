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
};

// Сегмент показывается только на узких экранах (CSS .tview-toggle);
// на десктопе всегда полный набор колонок.
export function TableViewToggle({ value, onChange, fullLabel = "Полно" }: TableViewToggleProps) {
  return (
    <div className="tview-toggle" role="group" aria-label="Набор колонок таблицы">
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
