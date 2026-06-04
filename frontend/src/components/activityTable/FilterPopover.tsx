import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

type FilterPopoverProps = {
  open: boolean;
  anchorEl: HTMLElement | null;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
};

export function FilterPopover({ open, anchorEl, onClose, title, children, footer }: FilterPopoverProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ top: 0, left: 0 });

  useEffect(() => {
    if (!open || !anchorEl) {
      return;
    }
    const updatePosition = () => {
      const rect = anchorEl.getBoundingClientRect();
      const panelWidth = 260;
      let left = rect.left;
      if (left + panelWidth > window.innerWidth - 8) {
        left = Math.max(8, window.innerWidth - panelWidth - 8);
      }
      setPosition({ top: rect.bottom + 6, left });
    };
    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open, anchorEl]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (anchorEl?.contains(target) || panelRef.current?.contains(target)) {
        return;
      }
      onClose();
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open, anchorEl, onClose]);

  if (!open) {
    return null;
  }

  return createPortal(
    <div
      ref={panelRef}
      className="filter-popover"
      style={{ top: position.top, left: position.left }}
      role="dialog"
      aria-label={title}
    >
      <div className="filter-popover-header">
        <span className="filter-popover-title">{title}</span>
      </div>
      <div className="filter-popover-body">{children}</div>
      {footer && <div className="filter-popover-footer">{footer}</div>}
    </div>,
    document.body,
  );
}
