import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

type ConfirmModalProps = {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  confirmLoading?: boolean;
  // Подтверждение недоступно, пока в теле модалки не сделан обязательный выбор.
  confirmDisabled?: boolean;
  variant?: "default" | "danger";
  onConfirm: () => void;
  onCancel: () => void;
};

export function ConfirmModal({
  open,
  title,
  children,
  confirmLabel = "Подтвердить",
  cancelLabel = "Отмена",
  confirmLoading = false,
  confirmDisabled = false,
  variant = "default",
  onConfirm,
  onCancel,
}: ConfirmModalProps) {
  useEffect(() => {
    if (!open) {
      return;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !confirmLoading) {
        onCancel();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open, confirmLoading, onCancel]);

  if (!open) {
    return null;
  }

  return createPortal(
    <div className="modal-overlay" onClick={confirmLoading ? undefined : onCancel}>
      <div
        className="modal-panel"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-modal-title"
        aria-describedby="confirm-modal-desc"
        onClick={(event) => event.stopPropagation()}
      >
        {variant === "danger" && (
          <div className="modal-icon modal-icon-danger" aria-hidden>
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
            </svg>
          </div>
        )}
        <h2 id="confirm-modal-title" className="modal-title">
          {title}
        </h2>
        <div id="confirm-modal-desc" className="modal-body">
          {children}
        </div>
        <div className="modal-actions">
          <button
            type="button"
            className="btn secondary modal-btn"
            onClick={onCancel}
            disabled={confirmLoading}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            className={`btn modal-btn ${variant === "danger" ? "btn-danger-confirm" : "primary"}`}
            onClick={onConfirm}
            disabled={confirmLoading || confirmDisabled}
          >
            {confirmLoading ? "Подождите…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
