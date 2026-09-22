import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { PlatformBadge } from "./PlatformBadge";
import { platformCodeLabel } from "../lib/format";

type QrCodeModalProps = {
  open: boolean;
  platformCode: string;
  displayName: string | null;
  code: string;
  onClose: () => void;
};

export function QrCodeModal({ open, platformCode, displayName, code, onClose }: QrCodeModalProps) {
  const [dataUrl, setDataUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    let cancelled = false;
    // Библиотека едет отдельным чанком и грузится при первом открытии модалки —
    // в стартовом бандле ей делать нечего. Параметры — те же, что у штатных QR
    // систем: режим alphanumeric (код — это «A» + цифры) и повышенная коррекция
    // ошибок, чтобы код читался с экрана телефона — бликующего, с невыключенной
    // ночной подсветкой и отпечатками.
    import("qrcode")
      .then(({ default: QRCode }) =>
        QRCode.toDataURL(code, {
          margin: 1,
          width: 320,
          errorCorrectionLevel: "Q",
          color: { dark: "#0f172a", light: "#ffffff" },
        }),
      )
      .then((url) => {
        if (!cancelled) {
          setDataUrl(url);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setDataUrl(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open, code]);

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
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-panel qr-modal-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="qr-modal-title"
        onClick={(event) => event.stopPropagation()}
      >
        <button type="button" className="modal-close-btn qr-modal-close" aria-label="Закрыть" onClick={onClose}>
          ×
        </button>
        <div className="qr-modal-head">
          <PlatformBadge code={platformCode} />
        </div>
        {displayName && <p className="qr-modal-name">{displayName}</p>}
        <p id="qr-modal-title" className="qr-modal-code">{code}</p>
        <div className="qr-modal-image-wrap">
          {dataUrl ? (
            <img src={dataUrl} alt={`QR-код участника ${code}`} className="qr-modal-image" width={320} height={320} />
          ) : (
            <div className="qr-modal-image-placeholder" aria-hidden="true" />
          )}
        </div>
        <p className="qr-modal-hint muted">
          Покажите этот экран сканеру на финише. Код работает только на стартах{" "}
          {platformCodeLabel(platformCode)}.
        </p>
      </div>
    </div>,
    document.body,
  );
}
