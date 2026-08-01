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
    // Просто overflow:hidden на body не держит iOS Safari — фон всё равно
    // скроллится тачем (rubber-banding). Фиксируем body на текущей позиции
    // и возвращаем скролл на место при закрытии.
    const scrollY = window.scrollY;
    const body = document.body;
    const previousPosition = body.style.position;
    const previousTop = body.style.top;
    const previousWidth = body.style.width;
    const previousOverflow = body.style.overflow;
    body.style.position = "fixed";
    body.style.top = `-${scrollY}px`;
    body.style.width = "100%";
    body.style.overflow = "hidden";
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      body.style.position = previousPosition;
      body.style.top = previousTop;
      body.style.width = previousWidth;
      body.style.overflow = previousOverflow;
      window.scrollTo(0, scrollY);
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
