import { useCallback, useState } from "react";

export type TableView = "short" | "full";

// Режим «Кратко | Полно» живёт per-таблица и переживает перезагрузку.
export function useTableView(storageKey: string): [TableView, (view: TableView) => void] {
  const fullKey = `tableView:${storageKey}`;
  const [view, setViewState] = useState<TableView>(() => {
    try {
      return localStorage.getItem(fullKey) === "full" ? "full" : "short";
    } catch {
      return "short";
    }
  });

  const setView = useCallback(
    (next: TableView) => {
      setViewState(next);
      try {
        localStorage.setItem(fullKey, next);
      } catch {
        // приватный режим — просто не сохраняем
      }
    },
    [fullKey],
  );

  return [view, setView];
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
