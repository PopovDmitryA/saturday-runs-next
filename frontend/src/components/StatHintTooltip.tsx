import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

type StatHintTooltipProps = {
  text?: string;
  content?: ReactNode;
  children: ReactNode;
  className?: string;
};

export function StatHintTooltip({ text, content, children, className }: StatHintTooltipProps) {
  const triggerRef = useRef<HTMLSpanElement>(null);
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

  useEffect(() => {
    if (!visible) {
      return;
    }
    const handleReposition = () => updatePosition();
    window.addEventListener("scroll", handleReposition, true);
    window.addEventListener("resize", handleReposition);
    return () => {
      window.removeEventListener("scroll", handleReposition, true);
      window.removeEventListener("resize", handleReposition);
    };
  }, [visible]);

  return (
    <>
      <span
        ref={triggerRef}
        className={className ?? "stat-hint-tooltip-trigger"}
        onMouseEnter={show}
        onMouseLeave={hide}
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
