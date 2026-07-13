import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

type DetailModalProps = {
  open: boolean;
  title: string;
  children: ReactNode;
  onClose: () => void;
  footer?: ReactNode;
};

export function DetailModal({ open, title, children, onClose, footer }: DetailModalProps) {
  // Закрывать оверлей только если и mousedown, и click пришлись мимо панели —
  // иначе выделение текста мышью (drag начался внутри, отпустили за пределами)
  // закрывает модалку и сбрасывает несохранённый ввод.
  const overlayMouseDownRef = useRef(false);

  useEffect(() => {
    if (!open) {
      return;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open, onClose]);

  if (!open) {
    return null;
  }

  return createPortal(
    <div
      className="modal-overlay"
      onMouseDown={(event) => {
        overlayMouseDownRef.current = event.target === event.currentTarget;
      }}
      onClick={(event) => {
        if (overlayMouseDownRef.current && event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <div
        className="modal-panel modal-panel-wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="detail-modal-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header-row">
          <h2 id="detail-modal-title" className="modal-title modal-title-left">
            {title}
          </h2>
          <button type="button" className="modal-close-btn" onClick={onClose} aria-label="Закрыть">
            ×
          </button>
        </div>
        <div className="modal-body modal-body-scroll">{children}</div>
        {footer ? <div className="modal-footer">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}
