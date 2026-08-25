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
  /** Минимальная ширина таблицы в кратком виде. */
  minWidth: number;
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
    minWidth: adaptive.minWidth,
    hasToggle: !adaptive.showsEverything,
  };
}
