import { useEffect, useState } from "react";

/**
 * Тач-подсказки: на телефоне hover не существует, поэтому все тултипы,
 * которые на десктопе живут на :hover (нативный title и CSS-подсказки
 * графиков), открываются тапом. Правило одно на весь сайт: тап показывает
 * подсказку только там, где он ничего больше не делает — по кнопкам, ссылкам
 * и сортировке колонок тап работает как обычно, тултип не перехватывает.
 */

/**
 * Элементы, у которых тап — это действие, а не «посмотреть подсказку».
 * data-tap-tooltip="off" — ручной отказ для кликабельных обёрток без тега
 * (например, строка таблицы с onClick, которую по разметке не отличить).
 */
export const TAP_TOOLTIP_INTERACTIVE_SELECTOR = [
  "a",
  "button",
  "input",
  "select",
  "textarea",
  "label",
  "summary",
  '[role="button"]',
  '[role="link"]',
  '[role="tab"]',
  '[contenteditable="true"]',
  '[data-tap-tooltip="off"]',
].join(", ");

/** Тап по этому месту что-то делает сам — подсказку не показываем. */
export function isTapTooltipBlocked(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) {
    return true;
  }
  return target.closest(TAP_TOOLTIP_INTERACTIVE_SELECTOR) !== null;
}

const COARSE_QUERY = "(hover: none)";

function matchCoarse(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia(COARSE_QUERY).matches;
}

/**
 * Устройство без мыши. На десктопе тап-режим не включаем: там hover работает,
 * а «клик оставляет подсказку висеть» — только мешает.
 */
export function useCoarsePointer(): boolean {
  const [coarse, setCoarse] = useState(matchCoarse);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const mql = window.matchMedia(COARSE_QUERY);
    const onChange = (event: MediaQueryListEvent) => setCoarse(event.matches);
    setCoarse(mql.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return coarse;
}
