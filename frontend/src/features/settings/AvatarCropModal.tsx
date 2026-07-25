import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/** Сторона квадратного окна кадрирования (CSS-пиксели). */
const VIEWPORT = 280;
/** Сторона итоговой картинки, отправляемой на сервер (бэкенд ужмёт до 256). */
const EXPORT_SIZE = 512;
const MAX_ZOOM = 4;

/**
 * Кадрирование аватарки перед загрузкой: превью в квадратном окне,
 * перетаскивание мышью/пальцем и зум ползунком или колесом — как на
 * «больших» сайтах. Итог выгружается квадратным JPEG через canvas, так что
 * на сервер уходит уже маленький файл нужного кадра.
 */
export function AvatarCropModal({
  file,
  busy = false,
  onCancel,
  onConfirm,
}: {
  file: File;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: (blob: Blob) => void;
}) {
  const url = useMemo(() => URL.createObjectURL(file), [file]);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [error, setError] = useState(false);
  const dragRef = useRef<{ pointerId: number; startX: number; startY: number; baseX: number; baseY: number } | null>(
    null,
  );

  useEffect(() => () => URL.revokeObjectURL(url), [url]);

  // Кадр не может быть меньше окна: базовый масштаб — «cover».
  const baseScale = natural ? VIEWPORT / Math.min(natural.w, natural.h) : 1;
  const scale = baseScale * zoom;

  const clampOffset = useCallback(
    (x: number, y: number, atScale: number) => {
      if (!natural) {
        return { x, y };
      }
      const minX = VIEWPORT - natural.w * atScale;
      const minY = VIEWPORT - natural.h * atScale;
      return {
        x: Math.min(0, Math.max(minX, x)),
        y: Math.min(0, Math.max(minY, y)),
      };
    },
    [natural],
  );

  const handleLoad = () => {
    const img = imgRef.current;
    if (!img) {
      return;
    }
    const dims = { w: img.naturalWidth, h: img.naturalHeight };
    setNatural(dims);
    // Стартовый кадр — по центру.
    const startScale = VIEWPORT / Math.min(dims.w, dims.h);
    setOffset({
      x: (VIEWPORT - dims.w * startScale) / 2,
      y: (VIEWPORT - dims.h * startScale) / 2,
    });
  };

  const applyZoom = (nextZoom: number) => {
    const clampedZoom = Math.min(MAX_ZOOM, Math.max(1, nextZoom));
    const nextScale = baseScale * clampedZoom;
    // Центр окна остаётся на месте при изменении масштаба.
    setOffset((current) => {
      const centerImgX = (VIEWPORT / 2 - current.x) / scale;
      const centerImgY = (VIEWPORT / 2 - current.y) / scale;
      return clampOffset(VIEWPORT / 2 - centerImgX * nextScale, VIEWPORT / 2 - centerImgY * nextScale, nextScale);
    });
    setZoom(clampedZoom);
  };

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      baseX: offset.x,
      baseY: offset.y,
    };
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) {
      return;
    }
    setOffset(
      clampOffset(drag.baseX + (event.clientX - drag.startX), drag.baseY + (event.clientY - drag.startY), scale),
    );
  };

  const handlePointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId === event.pointerId) {
      dragRef.current = null;
    }
  };

  const handleWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    applyZoom(zoom * (event.deltaY < 0 ? 1.08 : 1 / 1.08));
  };

  const handleConfirm = () => {
    const img = imgRef.current;
    if (!img || !natural) {
      return;
    }
    const canvas = document.createElement("canvas");
    canvas.width = EXPORT_SIZE;
    canvas.height = EXPORT_SIZE;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      setError(true);
      return;
    }
    ctx.imageSmoothingQuality = "high";
    const sx = -offset.x / scale;
    const sy = -offset.y / scale;
    const sSize = VIEWPORT / scale;
    ctx.drawImage(img, sx, sy, sSize, sSize, 0, 0, EXPORT_SIZE, EXPORT_SIZE);
    canvas.toBlob(
      (blob) => {
        if (blob) {
          onConfirm(blob);
        } else {
          setError(true);
        }
      },
      "image/jpeg",
      0.92,
    );
  };

  return (
    <div className="avatar-crop-overlay" role="dialog" aria-modal="true" aria-label="Кадрирование аватара">
      <div className="avatar-crop-panel">
        <h3 className="avatar-crop-title">Выберите кадр</h3>
        <div
          className="avatar-crop-viewport"
          style={{ width: VIEWPORT, height: VIEWPORT }}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
          onWheel={handleWheel}
        >
          <img
            ref={imgRef}
            src={url}
            alt=""
            draggable={false}
            onLoad={handleLoad}
            onError={() => setError(true)}
            style={
              natural
                ? {
                    width: natural.w * scale,
                    height: natural.h * scale,
                    transform: `translate(${offset.x}px, ${offset.y}px)`,
                  }
                : undefined
            }
          />
          <span className="avatar-crop-frame" aria-hidden="true" />
        </div>
        <div className="avatar-crop-zoom">
          <span aria-hidden="true">−</span>
          <input
            type="range"
            min={1}
            max={MAX_ZOOM}
            step={0.01}
            value={zoom}
            aria-label="Масштаб"
            onChange={(event) => applyZoom(Number(event.target.value))}
          />
          <span aria-hidden="true">+</span>
        </div>
        <p className="muted avatar-crop-hint">Перетащите фото и подберите масштаб.</p>
        {error && <p className="error-text">Не удалось обработать изображение — попробуйте другой файл.</p>}
        <div className="avatar-crop-actions">
          <button type="button" className="btn secondary" onClick={onCancel} disabled={busy}>
            Отмена
          </button>
          <button type="button" className="btn primary" onClick={handleConfirm} disabled={busy || !natural}>
            {busy ? "Сохраняем…" : "Сохранить"}
          </button>
        </div>
      </div>
    </div>
  );
}
