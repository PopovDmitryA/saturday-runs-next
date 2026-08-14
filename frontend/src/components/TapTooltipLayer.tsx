import { useCallback, useEffect, useState } from "react";
import { TapTooltipBubble } from "./TapTooltipBubble";
import { isTapTooltipBlocked, useCoarsePointer } from "../lib/tapTooltip";

type TitleTooltip = { anchor: HTMLElement; lines: string[] };

/**
 * Глобальный слой подсказок для тач-устройств: тап по любому элементу с
 * атрибутом title показывает его текст всплывающим окном. На десктопе слой
 * молчит — там тот же title показывает браузер по наведению.
 *
 * Один слой на весь сайт вместо правки каждого места: title расставлен в
 * десятках компонентов (буквы «Алфавита», дни календаря челленджей, медали,
 * заголовки колонок, бейджи систем), и на телефоне все они были немыми.
 */
export function TapTooltipLayer() {
  const coarse = useCoarsePointer();
  const [tooltip, setTooltip] = useState<TitleTooltip | null>(null);

  const hide = useCallback(() => setTooltip(null), []);

  useEffect(() => {
    if (!coarse) {
      setTooltip(null);
      return;
    }
    const handleClick = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Element)) {
        hide();
        return;
      }
      const trigger = target.closest<HTMLElement>("[title]");
      const text = trigger?.getAttribute("title")?.trim();
      // Тап мимо подсказки закрывает открытую; тап по кликабельному — тоже:
      // там у тапа своё дело (ссылка, кнопка, сортировка колонки), и
      // подсказка не должна висеть поверх нового экрана.
      if (!trigger || !text || isTapTooltipBlocked(target)) {
        hide();
        return;
      }
      setTooltip((current) =>
        // Повторный тап по тому же месту закрывает подсказку.
        current?.anchor === trigger
          ? null
          : {
              anchor: trigger,
              lines: text.split("\n").filter((line) => line.trim().length > 0),
            },
      );
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        hide();
      }
    };
    // Перехват на фазе погружения: внутри модалок панель гасит всплытие
    // (event.stopPropagation), и обычный слушатель на document до тапа по
    // подсказке внутри модалки просто не доходил бы.
    document.addEventListener("click", handleClick, true);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("click", handleClick, true);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [coarse, hide]);

  useEffect(() => {
    if (!tooltip) {
      return;
    }
    // Позицию считаем один раз по месту тапа: на скролле и повороте экрана
    // подсказку проще убрать, чем таскать за элементом.
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    return () => {
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
    };
  }, [tooltip, hide]);

  if (!coarse || !tooltip) {
    return null;
  }

  return <TapTooltipBubble anchor={tooltip.anchor} lines={tooltip.lines} />;
}
