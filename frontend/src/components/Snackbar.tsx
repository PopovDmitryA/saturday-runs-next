import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

type SnackbarProps = {
  open: boolean;
  title?: string;
  variant?: "default" | "error";
  children: ReactNode;
  onDismiss: () => void;
  autoHideMs?: number;
};

export function Snackbar({
  open,
  title = "Уведомление",
  variant = "default",
  children,
  onDismiss,
  autoHideMs = 8000,
}: SnackbarProps) {
  useEffect(() => {
    if (!open) {
      return;
    }
    const timer = window.setTimeout(onDismiss, autoHideMs);
    return () => window.clearTimeout(timer);
  }, [open, autoHideMs, onDismiss]);

  if (!open) {
    return null;
  }

  return createPortal(
    <div className="snackbar" role={variant === "error" ? "alert" : "status"} aria-live="polite">
      <div className={`snackbar-panel${variant === "error" ? " snackbar-panel-error" : ""}`}>
        <div className={`snackbar-panel-header${variant === "error" ? " snackbar-panel-header-error" : ""}`}>
          <span className={`snackbar-panel-title${variant === "error" ? " snackbar-panel-title-error" : ""}`}>
            {title}
          </span>
          <button type="button" className="snackbar-dismiss" aria-label="Закрыть" onClick={onDismiss}>
            ×
          </button>
        </div>
        <div className="snackbar-panel-body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
