import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

const VIEWPORT_GUTTER = 8;

type StatHintTooltipProps = {
  text?: string;
  content?: ReactNode;
  children: ReactNode;
  className?: string;
};

export function StatHintTooltip({ text, content, children, className }: StatHintTooltipProps) {
  const triggerRef = useRef<HTMLSpanElement>(null);
  const tooltipRef = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(false);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const tooltipBody = content ?? text;

  if (!tooltipBody) {
    return <>{children}</>;
  }

  const updatePosition = () => {
    const trigger = triggerRef.current;
    if (!trigger) {
      return;
    }
    const rect = trigger.getBoundingClientRect();
    setPosition({
      x: rect.left + rect.width / 2,
      y: rect.top - 8,
    });
  };

  const show = () => {
    updatePosition();
    setVisible(true);
  };

  const hide = () => {
    setVisible(false);
  };


  // Тултип центрируется по триггеру (transform: translateX(-50%)), поэтому у
  // правого края экрана он вылезал за границу — у колонки PR подсказка
  // обрезалась. После рендера меряем реальную ширину и вжимаем в экран.
  useLayoutEffect(() => {
    const tip = tooltipRef.current;
    if (!visible || !tip) {
      return;
    }
    const half = tip.offsetWidth / 2;
    const min = VIEWPORT_GUTTER + half;
    const max = window.innerWidth - VIEWPORT_GUTTER - half;
    const clamped = Math.min(Math.max(position.x, min), Math.max(min, max));
    if (Math.abs(clamped - position.x) > 0.5) {
      setPosition((current) => ({ ...current, x: clamped }));
    }
  }, [visible, position.x]);

  useEffect(() => {
    if (!visible) {
      return;
    }
    const handleReposition = () => updatePosition();
    // Тап мимо триггера закрывает тултип (десктопный mouseleave на таче не сработает).
    const handleOutside = (event: MouseEvent | TouchEvent) => {
      const trigger = triggerRef.current;
      if (trigger && event.target instanceof Node && !trigger.contains(event.target)) {
        setVisible(false);
      }
    };
    window.addEventListener("scroll", handleReposition, true);
    window.addEventListener("resize", handleReposition);
    document.addEventListener("click", handleOutside);
    return () => {
      window.removeEventListener("scroll", handleReposition, true);
      window.removeEventListener("resize", handleReposition);
      document.removeEventListener("click", handleOutside);
    };
  }, [visible]);

  return (
    <>
      <span
        ref={triggerRef}
        className={className ?? "stat-hint-tooltip-trigger"}
        onMouseEnter={show}
        onMouseLeave={hide}
        // Тап на тачскринах: mouseenter там не живёт, показываем по клику;
        // закрытие — тапом мимо (см. handleOutside). stopPropagation глушит
        // клик-сортировку на th, чтобы тап по «?» не менял сортировку.
        onClick={(event) => {
          event.stopPropagation();
          show();
        }}
        onFocus={show}
        onBlur={hide}
        tabIndex={0}
        aria-describedby={visible ? "stat-hint-tooltip" : undefined}
      >
        {children}
      </span>
      {visible &&
        createPortal(
          <span
            ref={tooltipRef}
            id="stat-hint-tooltip"
            role="tooltip"
            className="unique-locations-count-tooltip stat-hint-tooltip"
            style={{ left: position.x, top: position.y }}
          >
            {tooltipBody}
          </span>,
          document.body,
        )}
    </>
  );
}
