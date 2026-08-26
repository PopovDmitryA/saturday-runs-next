import { useEffect, useRef, useState, type ReactNode } from "react";
import { TapTooltipBubble } from "./TapTooltipBubble";
import { useCoarsePointer } from "../lib/tapTooltip";

/**
 * Зона, внутри которой наведение мышью на элемент с title показывает ту же
 * тёмную плашку, что и тап на телефоне.
 *
 * Нативная подсказка браузера для сеток из мелких клеток не годится: она
 * приходит с секундной задержкой и рисуется системным шрифтом мимо темы сайта.
 * В «Коллекциях» плашка своя и мгновенная (ChartColumnTooltip), и рядом с ней
 * буквы «Алфавита» и дни «Круглого года» читались как немые.
 *
 * Плашку рисует TapTooltipBubble — порталом в body: сетка «Круглого года»
 * лежит в горизонтально скроллящемся контейнере, и абсолютная плашка внутри
 * него обрезалась бы его краем.
 *
 * Слушатель один на всю сетку (делегирование), а не обёртка на каждую клетку:
 * дней в году 366, а букв — 28.
 */
export function TitleTooltipZone({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  // На телефоне hover не наступает никогда: там ту же плашку по тому же title
  // открывает тап — глобальный TapTooltipLayer.
  const coarse = useCoarsePointer();
  const ref = useRef<HTMLDivElement>(null);
  const [tooltip, setTooltip] = useState<{ anchor: HTMLElement; lines: string[] } | null>(null);
  // Закрыть подсказку умеют двое: сама зона (мышь ушла) и скролл. Оба обязаны
  // вернуть на место снятый title, поэтому закрытие живёт в одном месте.
  const leaveRef = useRef<() => void>(() => {});

  useEffect(() => {
    const node = ref.current;
    if (!node || coarse) {
      setTooltip(null);
      return;
    }
    // Нативную подсказку на время наведения снимаем: иначе поверх нашей плашки
    // через секунду выезжает вторая, системная. Атрибут возвращаем на место —
    // им же живут тап-подсказки и скринридеры.
    let current: HTMLElement | null = null;
    const restore = () => {
      if (!current) {
        return;
      }
      const stashed = current.dataset.titleStash;
      if (stashed !== undefined) {
        current.setAttribute("title", stashed);
        delete current.dataset.titleStash;
      }
      current = null;
    };
    const leave = () => {
      restore();
      setTooltip(null);
    };
    leaveRef.current = leave;
    const over = (event: MouseEvent) => {
      const target = event.target;
      const trigger =
        target instanceof Element
          ? target.closest<HTMLElement>("[title], [data-title-stash]")
          : null;
      if (!trigger || !node.contains(trigger) || trigger === node) {
        leave();
        return;
      }
      if (trigger === current) {
        return;
      }
      restore();
      const title = trigger.getAttribute("title");
      if (title !== null) {
        trigger.dataset.titleStash = title;
        trigger.removeAttribute("title");
      }
      current = trigger;
      const lines = (trigger.dataset.titleStash ?? "")
        .split("\n")
        .map((line) => line.trim())
        .filter((line) => line.length > 0);
      setTooltip(lines.length > 0 ? { anchor: trigger, lines } : null);
    };
    node.addEventListener("mouseover", over);
    node.addEventListener("mouseleave", leave);
    return () => {
      node.removeEventListener("mouseover", over);
      node.removeEventListener("mouseleave", leave);
      leave();
    };
  }, [coarse]);

  useEffect(() => {
    if (!tooltip) {
      return;
    }
    // Позиция посчитана один раз по месту клетки, поэтому на скролле и повороте
    // экрана плашку проще убрать, чем таскать за ней (так же в TapTooltipLayer).
    const hide = () => leaveRef.current();
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    return () => {
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
    };
  }, [tooltip]);

  return (
    <div ref={ref} className={className}>
      {children}
      {tooltip && <TapTooltipBubble anchor={tooltip.anchor} lines={tooltip.lines} />}
    </div>
  );
}
