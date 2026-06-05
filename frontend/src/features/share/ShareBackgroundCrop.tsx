import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ShareFormat } from "./shareConfig";
import {
  cropImageToFormat,
  defaultCropAdjust,
  imageElementFromCanvas,
  loadImageFromFile,
  revokeImageObjectUrl,
  type CropAdjust,
} from "./shareCustomBackground";

type ShareBackgroundCropProps = {
  format: ShareFormat;
  initialSource?: HTMLImageElement | null;
  onApply: (image: HTMLImageElement) => void;
  onSourceChange?: (image: HTMLImageElement) => void;
  onCancel?: () => void;
};

export function ShareBackgroundCrop({
  format,
  initialSource = null,
  onApply,
  onSourceChange,
  onCancel,
}: ShareBackgroundCropProps) {
  const [source, setSource] = useState<HTMLImageElement | null>(initialSource);
  const [adjust, setAdjust] = useState<CropAdjust>(defaultCropAdjust());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const dragStart = useRef({ x: 0, y: 0, offsetX: 0, offsetY: 0 });
  const previewRef = useRef<HTMLCanvasElement>(null);

  const aspectRatio = `${format.width} / ${format.height}`;

  useEffect(() => {
    if (initialSource) {
      setSource(initialSource);
      setAdjust(defaultCropAdjust());
    }
  }, [initialSource]);

  const drawPreview = useCallback(() => {
    if (!source || !previewRef.current) {
      return;
    }
    const cropped = cropImageToFormat(source, format.width, format.height, adjust);
    const ctx = previewRef.current.getContext("2d");
    if (!ctx) {
      return;
    }
    previewRef.current.width = format.width;
    previewRef.current.height = format.height;
    ctx.drawImage(cropped, 0, 0);
  }, [adjust, format.height, format.width, source]);

  useEffect(() => {
    drawPreview();
  }, [drawPreview]);

  const loadFile = async (file: File) => {
    if (!file.type.startsWith("image/")) {
      setError("Выберите файл изображения (JPG, PNG, WebP)");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const img = await loadImageFromFile(file);
      setSource((prev) => {
        revokeImageObjectUrl(prev);
        return img;
      });
      setAdjust(defaultCropAdjust());
      onSourceChange?.(img);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить файл");
    } finally {
      setLoading(false);
    }
  };

  const onFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) {
      return;
    }
    await loadFile(file);
  };

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!source) {
      return;
    }
    setDragging(true);
    dragStart.current = {
      x: event.clientX,
      y: event.clientY,
      offsetX: adjust.offsetX,
      offsetY: adjust.offsetY,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging) {
      return;
    }
    const dx = event.clientX - dragStart.current.x;
    const dy = event.clientY - dragStart.current.y;
    setAdjust((prev) => ({
      ...prev,
      offsetX: dragStart.current.offsetX + dx,
      offsetY: dragStart.current.offsetY + dy,
    }));
  };

  const onPointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    setDragging(false);
    event.currentTarget.releasePointerCapture(event.pointerId);
  };

  const handleApply = async () => {
    if (!source) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const cropped = cropImageToFormat(source, format.width, format.height, adjust);
      const image = await imageElementFromCanvas(cropped);
      onApply(image);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось обрезать фото");
    } finally {
      setLoading(false);
    }
  };

  const scaleLabel = useMemo(() => `${Math.round(adjust.scale * 100)}%`, [adjust.scale]);

  return (
    <div className="share-crop-panel">
      <p className="muted share-section-lead">
        Подгоните кадр под формат «{format.title}». Фото хранится только в браузере.
      </p>

      {!initialSource && (
        <label className="btn secondary share-file-label">
          {loading ? "Загрузка…" : source ? "Другое фото" : "Выбрать фото"}
          <input type="file" accept="image/*" className="share-file-input" onChange={(e) => void onFileChange(e)} />
        </label>
      )}

      {initialSource && source && (
        <label className="btn secondary share-file-label">
          {loading ? "Загрузка…" : "Другое фото"}
          <input type="file" accept="image/*" className="share-file-input" onChange={(e) => void onFileChange(e)} />
        </label>
      )}

      {error && <p className="error-text">{error}</p>}

      {source && (
        <>
          <div
            className={`share-crop-preview-wrap ${dragging ? "dragging" : ""}`}
            style={{ aspectRatio }}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
          >
            <canvas ref={previewRef} className="share-crop-preview-canvas" />
            <span className="share-crop-hint">Перетащите, чтобы сдвинуть кадр</span>
          </div>

          <label className="share-crop-scale">
            Масштаб: {scaleLabel}
            <input
              type="range"
              min={1}
              max={2.5}
              step={0.01}
              value={adjust.scale}
              onChange={(e) => setAdjust((prev) => ({ ...prev, scale: Number(e.target.value) }))}
            />
          </label>

          <div className="actions-row">
            {onCancel && (
              <button type="button" className="btn secondary" onClick={onCancel}>
                Отмена
              </button>
            )}
            <button type="button" className="btn primary" disabled={loading} onClick={() => void handleApply()}>
              Применить кадр
            </button>
          </div>
        </>
      )}
    </div>
  );
}
