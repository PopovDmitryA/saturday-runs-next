import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";

const VIEWPORT_GUTTER = 8;
/** Отступ подсказки от элемента — столько же, сколько у StatHintTooltip. */
const ANCHOR_GAP = 8;
/** Если сверху меньше — показываем подсказку под элементом. */
const FLIP_THRESHOLD = 96;
/**
 * Насколько близко к краю подсказки можно подвинуть хвостик: скругление угла
 * 8px плюс половина самого хвостика (5px) — ближе он вылезает из-за уголка и
 * выглядит оторванным от плашки.
 */
const ARROW_EDGE_INSET = 14;

type Position = { x: number; y: number; below: boolean; arrow: number };

/**
 * Всплывающая подсказка тач-режима: рисуется порталом в body и вжимается в
 * экран. Порталом — потому что в графиках и сетках челленджей ячейка узкая и
 * зажата в скроллящемся контейнере: подсказка внутри неё обрезалась бы краем
 * контейнера, а у крайних столбцов уезжала бы за границу экрана.
 */
export function TapTooltipBubble({
  anchor,
  title,
  lines,
}: {
  anchor: HTMLElement | null;
  title?: string;
  lines: string[];
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<Position | null>(null);
  const contentKey = `${title ?? ""}\n${lines.join("\n")}`;

  useLayoutEffect(() => {
    const node = ref.current;
    if (!anchor || !node) {
      setPosition(null);
      return;
    }
    const rect = anchor.getBoundingClientRect();
    // Ширина у подсказки content-based (width: max-content), поэтому её можно
    // померить до того, как она встала на место.
    const width = node.offsetWidth;
    const half = width / 2;
    const min = VIEWPORT_GUTTER + half;
    const max = window.innerWidth - VIEWPORT_GUTTER - half;
    const centre = rect.left + rect.width / 2;
    const below = rect.top < FLIP_THRESHOLD;
    const x = Math.min(Math.max(centre, min), Math.max(min, max));
    // Хвостик смотрит на элемент, а не на середину плашки: у края экрана
    // подсказка вжимается в экран, её центр уезжает от элемента, и хвостик,
    // приклеенный к центру, показывал на соседнюю клетку (буква «Ц» в
    // «Алфавите» — самый правый столбец).
    setPosition({
      x,
      y: below ? rect.bottom + ANCHOR_GAP : rect.top - ANCHOR_GAP,
      below,
      arrow: Math.min(
        Math.max(centre - (x - half), ARROW_EDGE_INSET),
        Math.max(ARROW_EDGE_INSET, width - ARROW_EDGE_INSET),
      ),
    });
  }, [anchor, contentKey]);

  if (!anchor) {
    return null;
  }

  return createPortal(
    <div
      ref={ref}
      role="tooltip"
      className={`unique-locations-count-tooltip tap-tooltip${position?.below ? " tap-tooltip-below" : ""}`}
      style={
        position
          ? ({
              left: position.x,
              top: position.y,
              "--tap-tooltip-arrow": `${position.arrow}px`,
            } as CSSProperties)
          : { left: 0, top: 0, visibility: "hidden" }
      }
    >
      {title && <span className="tap-tooltip-title">{title}</span>}
      {lines.map((line, index) => (
        <span key={`${index}-${line}`} className="unique-locations-count-tooltip-line">
          {line}
        </span>
      ))}
    </div>,
    document.body,
  );
}
