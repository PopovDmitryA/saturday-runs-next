import { useState } from "react";
import { useAdaptiveColumns, type AdaptiveColumn } from "./useAdaptiveColumns";

export type TableView = "short" | "full";

export type TableColumns = {
  /** Что выбрано в сегменте «Кратко | Полно». */
  view: TableView;
  setView: (view: TableView) => void;
  /**
   * Нарисованы все колонки: либо выбрано «Полно», либо краткий вид и так всё
   * вместил — тогда переключать нечего и таблица сразу рисуется как полная.
   */
  showFull: boolean;
  /** Показывать ли колонку. */
  show: (key: string) => boolean;
  /** Ref на блок, по ширине которого набирается краткий вид. */
  measureRef: (node: HTMLElement | null) => void;
  /**
   * Минимальная ширина таблицы: `max(100%, Npx)`.
   *
   * Именно строка с max(), а не число. Голое число в inline-стиле перебивает
   * CSS-правило `min-width: 100%`, которым таблицы рейтингов растягиваются на
   * всю обёртку, — и таблица застывала на сумме своих колонок, оставляя
   * справа пустоту (880px в контейнере 1136px, Дмитрий 02.09.2026).
   * С max() оба условия выполняются разом: не уже суммы колонок (иначе
   * колонки схлопнутся) и не уже контейнера (иначе пустота справа).
   */
  minWidth: string;
  /** Нужен ли сегмент «Кратко | Полно»: краткий вид что-то прячет. */
  hasToggle: boolean;
};

/**
 * Всё про набор колонок таблицы в одном месте: режим «Кратко | Полно» и
 * подбор колонок под ширину экрана (useAdaptiveColumns).
 *
 * Режим всегда открывается кратким: это дефолт для телефона, а запоминание
 * выбора между заходами сбивало — страница неожиданно открывалась широкой
 * таблицей со скроллом.
 *
 * Если краткий вид на текущей ширине вместил все колонки, переключатель
 * бессмысленный: состав колонок от него не менялся, менялись только пропорции
 * (просьба Дмитрия 15.08.2026). В этом случае сегмент не рисуется вовсе, а
 * таблица сразу живёт по правилам полного вида — с его раскладкой колонок.
 */
export function useTableColumns(columns: AdaptiveColumn[]): TableColumns {
  const [view, setView] = useState<TableView>("short");
  const adaptive = useAdaptiveColumns(columns);
  const showFull = view === "full" || adaptive.showsEverything;

  return {
    view,
    setView,
    showFull,
    show: (key: string) => showFull || adaptive.isVisible(key),
    measureRef: adaptive.measureRef,
    // В полном виде минимум — сумма ВСЕХ колонок, а не только влезших: иначе
    // table-layout:fixed раздаёт фиксированные ширины остальным, а колонке без
    // ширины («Локация») достаётся остаток, и она схлопывалась до «Л…»
    // (Дмитрий 02.09.2026). С минимумом таблица честно листается вбок.
    minWidth: `max(100%, ${
      showFull ? columns.reduce((sum, column) => sum + column.width, 0) : adaptive.minWidth
    }px)`,
    hasToggle: !adaptive.showsEverything,
  };
}
