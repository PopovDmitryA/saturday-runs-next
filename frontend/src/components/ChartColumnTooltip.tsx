import { useEffect, useRef, useState, type PointerEvent, type ReactNode } from "react";
import { TapTooltipBubble } from "./TapTooltipBubble";
import { isTapTooltipBlocked, useCoarsePointer } from "../lib/tapTooltip";

export function ChartColumnTooltip({
  title,
  lines,
  children,
}: {
  title: string;
  lines: string[];
  children: ReactNode;
}) {
  // На телефоне hover не наступает никогда — там подсказку открывает тап.
  const coarse = useCoarsePointer();
  const [open, setOpen] = useState(false);
  const [hovered, setHovered] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!coarse && open) {
      setOpen(false);
    }
    if (coarse && hovered) {
      setHovered(false);
    }
  }, [coarse, open, hovered]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const handleClick = (event: MouseEvent) => {
      const wrap = wrapRef.current;
      if (wrap && event.target instanceof Node && wrap.contains(event.target)) {
        return;
      }
      setOpen(false);
    };
    // Фаза погружения — чтобы закрытие работало и внутри модалок, где панель
    // гасит всплытие клика (см. TapTooltipLayer).
    document.addEventListener("click", handleClick, true);
    return () => document.removeEventListener("click", handleClick, true);
  }, [open]);

  // Позицию портальная подсказка считает один раз при появлении, поэтому при
  // скролле или смене размеров окна её проще убрать, чем тащить за столбцом.
  // Ховерную — тоже: колесо мыши прокручивает страницу под неподвижным
  // курсором, pointerleave при этом не приходит.
  useEffect(() => {
    if (!open && !hovered) {
      return;
    }
    const close = () => {
      setOpen(false);
      setHovered(false);
    };
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [open, hovered]);

  // Подсказку и на десктопе рисует портальный TapTooltipBubble. Раньше здесь
  // жила CSS-плашка внутри колонки: у крайних столбцов она вылезала за край
  // экрана и обрезалась (Дмитрий 13.09.2026 — сентябрь на графике кабинета).
  // Журнал посещаемости лечил это руками, прижимая подсказку к краю клетки
  // через data-edge; портал вжимает её в экран сам и целится хвостиком в
  // столбец — одинаково во всех графиках сайта.
  const visible = coarse ? open : hovered;

  // Стилус и палец на гибридном устройстве шлют pointerenter, но hover там не
  // держится: подсказка залипла бы до следующего движения мышью.
  const handleEnter = (event: PointerEvent<HTMLDivElement>) => {
    if (event.pointerType === "mouse") {
      setHovered(true);
    }
  };
  const hide = () => setHovered(false);

  return (
    <div
      ref={wrapRef}
      className="analytics-chart-tooltip-wrap"
      onPointerEnter={coarse ? undefined : handleEnter}
      onPointerLeave={coarse ? undefined : hide}
      onFocus={coarse ? undefined : () => setHovered(true)}
      onBlur={coarse ? undefined : hide}
      onClick={
        coarse
          ? (event) => {
              if (isTapTooltipBlocked(event.target)) {
                return;
              }
              setOpen((value) => !value);
            }
          : undefined
      }
    >
      {children}
      {visible && <TapTooltipBubble anchor={wrapRef.current} title={title} lines={lines} />}
    </div>
  );
}
