import { useEffect, useRef, useState, type ReactNode } from "react";
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
  // На десктопе подсказку показывает :hover (см. index.css). На телефоне
  // hover не наступает никогда — там ту же подсказку открывает тап.
  const coarse = useCoarsePointer();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!coarse && open) {
      setOpen(false);
    }
  }, [coarse, open]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const close = () => setOpen(false);
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
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("click", handleClick, true);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [open]);

  return (
    <div
      ref={wrapRef}
      className="analytics-chart-tooltip-wrap"
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
      <div className="analytics-chart-tooltip" role="tooltip">
        <span className="analytics-chart-tooltip-title">{title}</span>
        {lines.map((line, index) => (
          <span key={`${index}-${line}`} className="analytics-chart-tooltip-line">
            {line}
          </span>
        ))}
      </div>
      {coarse && open && <TapTooltipBubble anchor={wrapRef.current} title={title} lines={lines} />}
    </div>
  );
}
