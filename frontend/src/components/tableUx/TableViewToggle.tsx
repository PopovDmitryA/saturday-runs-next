import type { TableColumns } from "./useTableColumns";

type TableViewToggleProps = {
  /** Результат useTableColumns: он же решает, нужен ли сегмент вообще. */
  columns: TableColumns;
  /**
   * Сегмент внутри панели фильтров: без собственной липкой полосы — панель
   * стоит над таблицей одним блоком, и вторая полоса поверх неё выглядела бы
   * отдельным этажом управления (правка Дмитрия 28.08.2026).
   */
  inline?: boolean;
};

/**
 * Сегмент «Кратко | Полно» в собственной липкой полосе (`.tview-bar`): полоса
 * прилипает под шапкой сайта, чтобы сменить набор колонок можно было из любого
 * места длинной таблицы, а не отматывая страницу назад (просьба Дмитрия
 * 11.08.2026).
 *
 * Ничего не рисуем, когда краткий вид и так показывает все колонки: раньше
 * сегмент висел на любой ширине и на широком мониторе «Полно» только меняло
 * пропорции, не добавляя ни одной колонки. Полоса пропадает вместе с сегментом,
 * иначе от неё осталась бы пустая строка над таблицей.
 */
export function TableViewToggle({ columns, inline = false }: TableViewToggleProps) {
  if (!columns.hasToggle) {
    return null;
  }

  return (
    <div className={inline ? "tview-inline" : "tview-bar"}>
      <div className="tview-toggle" role="group" aria-label="Набор колонок таблицы">
        <button
          type="button"
          aria-pressed={!columns.showFull}
          className={`tview-tab${columns.showFull ? "" : " tview-tab-active"}`}
          onClick={() => columns.setView("short")}
        >
          Кратко
        </button>
        <button
          type="button"
          aria-pressed={columns.showFull}
          className={`tview-tab${columns.showFull ? " tview-tab-active" : ""}`}
          onClick={() => columns.setView("full")}
        >
          Полно
        </button>
      </div>
    </div>
  );
}
