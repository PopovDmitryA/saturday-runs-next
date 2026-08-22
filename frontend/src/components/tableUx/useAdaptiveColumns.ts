import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Описание колонки для краткого вида таблицы.
 *
 * `width` — сколько места колонка занимает в пикселях (ровно то же число, что
 * стоит у неё в CSS: col-metric 9.25rem = 148px и т.д.). Первая колонка обычно
 * резиновая — ей задают минимально приличную ширину, остаток она заберёт сама.
 */
export type AdaptiveColumn = {
  key: string;
  width: number;
  /** Колонка краткого вида, которая показывается всегда — даже если не влезает. */
  required?: boolean;
};

export type AdaptiveColumns = {
  /** Ref на блок, по ширине которого считаем, сколько колонок влезло. */
  measureRef: (node: HTMLElement | null) => void;
  /** Показывать ли колонку в кратком виде. */
  isVisible: (key: string) => boolean;
  /** Суммарная ширина показанных колонок — минимальная ширина таблицы. */
  minWidth: number;
  /** Все ли колонки уместились: краткий вид совпал с полным. */
  showsEverything: boolean;
};

/**
 * Ширина контента до первого замера. Реальную даёт ResizeObserver, но до его
 * первого срабатывания таблица уже рисуется — с нулём она моргнула бы
 * минимальным набором колонок. Оценка: на широком макете от окна отъедают
 * сайдбар (236) с отступом (28) и поля страницы, на узком — только поля.
 */
function estimateWidth(): number {
  if (typeof window === "undefined") {
    return 0;
  }
  const viewport = Math.min(window.innerWidth, 1440);
  return viewport >= 1000 ? viewport - 340 : viewport - 40;
}

/**
 * Краткий вид таблицы — не фиксированный список колонок «для телефона», а
 * столько колонок, сколько влезает в текущую ширину (просьба Дмитрия
 * 11.08.2026). Колонки перечисляются В ПОРЯДКЕ ВАЖНОСТИ: обязательный минимум
 * идёт первым, дальше — то, что добавляем по мере расширения экрана. На
 * ультрашироком мониторе краткий вид доходит до полного, на телефоне
 * схлопывается до минимума (и листается вбок, если даже он не влез).
 *
 * Порядок вывода колонок в разметке при этом свой — он задаётся самой
 * таблицей, хук отвечает только за «показывать / не показывать».
 */
export function useAdaptiveColumns(columns: AdaptiveColumn[]): AdaptiveColumns {
  const [width, setWidth] = useState<number>(estimateWidth);
  const nodeRef = useRef<HTMLElement | null>(null);

  const measure = useCallback(() => {
    const node = nodeRef.current;
    if (node) {
      setWidth(node.getBoundingClientRect().width);
    }
  }, []);

  const measureRef = useCallback(
    (node: HTMLElement | null) => {
      nodeRef.current = node;
      if (!node) {
        return;
      }
      measure();
      const observer = new ResizeObserver(measure);
      observer.observe(node);
      // React 19 вызывает функцию, возвращённую ref-колбэком, при отвязке узла.
      return () => {
        observer.disconnect();
        nodeRef.current = null;
      };
    },
    [measure],
  );

  // ResizeObserver ловит и смену ширины окна, и сворачивание сайдбара, но
  // подстраховываемся событием resize: в части встроенных браузеров (панель
  // превью Claude Code) наблюдатель на смену размера окна не срабатывает
  // вовсе, и набор колонок застывал бы до перезагрузки страницы.
  useEffect(() => {
    let timer = 0;
    // Меряем сразу и ещё раз, когда раскладка устоялась: на первое событие
    // resize (и особенно на смену ориентации) ширина блока успевает приехать
    // ещё старая, и набор колонок застыл бы на промежуточном значении.
    const onResize = () => {
      measure();
      window.clearTimeout(timer);
      timer = window.setTimeout(measure, 200);
    };
    window.addEventListener("resize", onResize);
    window.addEventListener("orientationchange", onResize);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("orientationchange", onResize);
    };
  }, [measure]);

  const visible = new Set<string>();
  let used = 0;
  for (const column of columns) {
    if (column.required) {
      visible.add(column.key);
      used += column.width;
      continue;
    }
    if (used + column.width > width) {
      // Дальше колонки только менее важные: обрываем, чтобы набор рос
      // предсказуемо — «шире экран → строго больше колонок».
      break;
    }
    visible.add(column.key);
    used += column.width;
  }

  return {
    measureRef,
    isVisible: (key: string) => visible.has(key),
    minWidth: used,
    showsEverything: visible.size === columns.length,
  };
}
