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
   * Показывать сегмент и на компьютере: так сделано там, где краткий вид
   * убирает колонки на любой ширине (рейтинги, локации, журнал протоколов).
   * По умолчанию сегмент виден только на узких экранах.
   */
  alwaysVisible?: boolean;
};

/**
 * Сегмент «Кратко | Полно» в собственной липкой полосе (`.tview-bar`): полоса
 * прилипает под шапкой сайта, чтобы сменить набор колонок можно было из любого
 * места длинной таблицы, а не отматывая страницу назад (просьба Дмитрия
 * 11.08.2026). Полоса прячется вместе с сегментом, иначе на компьютере от неё
 * оставалась бы пустая строка.
 */
export function TableViewToggle({
  value,
  onChange,
  fullLabel = "Полно",
  alwaysVisible = false,
}: TableViewToggleProps) {
  return (
    <div className={`tview-bar${alwaysVisible ? " tview-bar-always" : ""}`}>
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
    </div>
  );
}
