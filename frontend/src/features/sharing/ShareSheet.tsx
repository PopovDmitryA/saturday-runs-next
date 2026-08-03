// Шторка «Поделиться»: постер готов сразу, ноль обязательных решений.
//
// Preview-first (паттерн Wrapped/Strava): свайп по лукам меняет «одежду»
// постера, переключатель форматов — 9:16 / 1:1 / широкий. «Настроить» — для
// энтузиастов: набор метрик, своё фото (drag+zoom прямо на превью).
//
// Экспорт: тот же React-компонент карточки рендерится оффскрином в нативном
// размере и снимается html-to-image (см. exportCard.ts).

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
} from "react";
import { createPortal } from "react-dom";
import {
  trackShareCustomize,
  trackShareSuccess,
  trackShareTemplateSwitch,
} from "./analytics";
import { exportCardToPng } from "./exportCard";
import { ensureShareFontsLoaded, shareFontFromQuery } from "./fonts";
import {
  DEFAULT_PHOTO_TRANSFORM,
  PHOTO_LOOK,
  looksFor,
  type PhotoTransform,
  type ShareLook,
  type SharePhoto,
} from "./looks";
import { canShareFiles, downloadBlob, shareOrDownload } from "./shareOut";
import { ShareCardView, metricLimit } from "./ShareCardView";
import type { ShareFormatId, ShareSubject } from "./types";
import { SHARE_FORMATS, shareFormat } from "./types";

function loadPhotoFile(file: File): Promise<SharePhoto> {
  return new Promise((resolve, reject) => {
    const objectUrl = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () =>
      resolve({ objectUrl, width: image.naturalWidth, height: image.naturalHeight, transforms: {} });
    image.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error("Не удалось прочитать фото"));
    };
    image.src = objectUrl;
  });
}

export function ShareSheet({
  subject,
  onClose,
}: {
  subject: ShareSubject;
  onClose: () => void;
}) {
  const font = shareFontFromQuery();
  const [formatId, setFormatId] = useState<ShareFormatId>(subject.defaultFormat);
  const looks = looksFor(subject.data.audience);
  const [lookId, setLookId] = useState<string>(looks[0].id);
  const [photo, setPhoto] = useState<SharePhoto | null>(null);
  const [customizeOpen, setCustomizeOpen] = useState(false);
  const [metricIds, setMetricIds] = useState<string[] | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [fontsReady, setFontsReady] = useState(false);
  const [scale, setScale] = useState(0.25);

  const format = shareFormat(formatId);
  const photoActive = photo !== null && lookId === PHOTO_LOOK.id;
  const look: ShareLook = photoActive
    ? PHOTO_LOOK
    : looks.find((item) => item.id === lookId) ?? looks[0];

  const previewBoxRef = useRef<HTMLDivElement | null>(null);
  const exportRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Шрифт постера: дожидаемся до первого рендера карточки.
  useEffect(() => {
    let cancelled = false;
    ensureShareFontsLoaded(font).then(() => {
      if (!cancelled) {
        setFontsReady(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [font]);

  // Body-lock, как в DetailModal: iOS не умеет overflow:hidden на body.
  useEffect(() => {
    const scrollY = window.scrollY;
    const { style } = document.body;
    const prev = { position: style.position, top: style.top, width: style.width };
    style.position = "fixed";
    style.top = `-${scrollY}px`;
    style.width = "100%";
    return () => {
      style.position = prev.position;
      style.top = prev.top;
      style.width = prev.width;
      window.scrollTo(0, scrollY);
    };
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Освобождаем objectUrl фото при закрытии шторки.
  useEffect(
    () => () => {
      if (photo) {
        URL.revokeObjectURL(photo.objectUrl);
      }
    },
    [photo],
  );

  // Масштаб превью: карточка нативного размера ужимается transform-ом.
  useLayoutEffect(() => {
    const box = previewBoxRef.current;
    if (!box) {
      return;
    }
    const compute = () => {
      const rect = box.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) {
        return;
      }
      setScale(Math.min(rect.width / format.width, rect.height / format.height));
    };
    compute();
    const observer = new ResizeObserver(compute);
    observer.observe(box);
    return () => observer.disconnect();
  }, [format.width, format.height]);

  const selectLook = (id: string) => {
    setLookId(id);
    trackShareTemplateSwitch("look", id);
  };

  const selectFormat = (id: ShareFormatId) => {
    setFormatId(id);
    trackShareTemplateSwitch("format", id);
  };

  const onPickPhoto = async (file: File | null | undefined) => {
    if (!file) {
      return;
    }
    try {
      const next = await loadPhotoFile(file);
      setPhoto((prev) => {
        if (prev) {
          URL.revokeObjectURL(prev.objectUrl);
        }
        return next;
      });
      setLookId(PHOTO_LOOK.id);
      trackShareCustomize("photo");
    } catch {
      // Битый файл — остаёмся на текущем луке.
    }
  };

  const removePhoto = () => {
    setPhoto((prev) => {
      if (prev) {
        URL.revokeObjectURL(prev.objectUrl);
      }
      return null;
    });
    setLookId(looks[0].id);
  };

  const updateTransform = useCallback(
    (updater: (prev: PhotoTransform) => PhotoTransform) => {
      setPhoto((prev) => {
        if (!prev) {
          return prev;
        }
        const current = prev.transforms[formatId] ?? DEFAULT_PHOTO_TRANSFORM;
        const next = updater(current);
        return {
          ...prev,
          transforms: { ...prev.transforms, [formatId]: next },
        };
      });
    },
    [formatId],
  );

  // Drag + pinch своего фото прямо на превью.
  const pointersRef = useRef(new Map<number, { x: number; y: number }>());
  const pinchDistanceRef = useRef<number | null>(null);

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!photoActive) {
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!photoActive) {
      return;
    }
    const pointers = pointersRef.current;
    const prev = pointers.get(event.pointerId);
    if (!prev) {
      return;
    }
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const points = [...pointers.values()];
    if (points.length >= 2) {
      const distance = Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
      const prevDistance = pinchDistanceRef.current;
      pinchDistanceRef.current = distance;
      if (prevDistance != null && prevDistance > 0) {
        const ratio = distance / prevDistance;
        updateTransform((transform) => ({
          ...transform,
          scale: Math.min(4, Math.max(1, transform.scale * ratio)),
        }));
      }
      return;
    }
    const dx = (event.clientX - prev.x) / (format.width * scale);
    const dy = (event.clientY - prev.y) / (format.height * scale);
    updateTransform((transform) => ({
      ...transform,
      offsetX: Math.min(0.5, Math.max(-0.5, transform.offsetX + dx)),
      offsetY: Math.min(0.5, Math.max(-0.5, transform.offsetY + dy)),
    }));
  };

  const onPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    pointersRef.current.delete(event.pointerId);
    if (pointersRef.current.size < 2) {
      pinchDistanceRef.current = null;
    }
  };

  const onWheel = (event: ReactWheelEvent<HTMLDivElement>) => {
    if (!photoActive) {
      return;
    }
    event.preventDefault();
    const factor = event.deltaY < 0 ? 1.06 : 1 / 1.06;
    updateTransform((transform) => ({
      ...transform,
      scale: Math.min(4, Math.max(1, transform.scale * factor)),
    }));
  };

  const exportPng = useCallback(async (): Promise<Blob> => {
    await ensureShareFontsLoaded(font);
    const node = exportRef.current;
    if (!node) {
      throw new Error("Экспортный узел не смонтирован");
    }
    return exportCardToPng(node, format.width, format.height);
  }, [font, format.width, format.height]);

  const systemShare = canShareFiles();

  const onShare = async () => {
    if (busy) {
      return;
    }
    setBusy(true);
    try {
      const blob = await exportPng();
      const outcome = await shareOrDownload(blob, subject.fileName, "Моя статистика — run5k.run");
      if (outcome === "system" || outcome === "download") {
        trackShareSuccess(outcome, subject.kind);
      }
    } catch {
      // Ошибка экспорта: кнопка вернётся в исходное состояние.
    } finally {
      setBusy(false);
    }
  };

  const onDownload = async () => {
    if (busy) {
      return;
    }
    setBusy(true);
    try {
      const blob = await exportPng();
      downloadBlob(blob, subject.fileName);
      trackShareSuccess("download", subject.kind);
    } catch {
      // см. onShare
    } finally {
      setBusy(false);
    }
  };

  const limit = metricLimit(format, subject.data);
  const toggleMetric = (id: string) => {
    const current = metricIds ?? subject.data.metrics.slice(0, limit).map((metric) => metric.id);
    const next = current.includes(id)
      ? current.filter((item) => item !== id)
      : current.length < limit
        ? [...current, id]
        : current;
    setMetricIds(next);
    trackShareCustomize("metrics");
  };

  const activeMetricIds = useMemo(
    () => metricIds ?? subject.data.metrics.slice(0, limit).map((metric) => metric.id),
    [metricIds, subject.data.metrics, limit],
  );

  const cardProps = {
    data: subject.data,
    format,
    look,
    photo: photoActive ? photo : null,
    font,
    visibleMetricIds: metricIds,
  };

  const body = (
    <div className="s2-overlay" role="presentation" onClick={(event) => event.target === event.currentTarget && onClose()}>
      <div className="s2-sheet" role="dialog" aria-modal="true" aria-label="Поделиться">
        <div className="s2-sheet-head">
          <div className="s2-sheet-title">Поделиться</div>
          <button type="button" className="s2-close" onClick={onClose} aria-label="Закрыть">
            ✕
          </button>
        </div>

        <div className="s2-preview-box" ref={previewBoxRef}>
          <div
            className={`s2-preview ${photoActive ? "s2-preview--pannable" : ""}`}
            style={{ width: format.width * scale, height: format.height * scale }}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
            onWheel={onWheel}
          >
            <div className="s2-preview-scale" style={{ transform: `scale(${scale})` }}>
              {fontsReady ? <ShareCardView {...cardProps} /> : null}
            </div>
          </div>
        </div>
        {photoActive ? <p className="s2-hint">Двигайте фото пальцем, зум — щипком или колесом</p> : null}

        <div className="s2-looks" role="tablist" aria-label="Оформление">
          {looks.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={!photoActive && item.id === look.id}
              className={`s2-look-chip ${!photoActive && item.id === look.id ? "s2-look-chip--active" : ""}`}
              onClick={() => selectLook(item.id)}
            >
              <span className="s2-look-swatch" style={{ background: item.background }} />
              <span className="s2-look-label">{item.label}</span>
            </button>
          ))}
          <button
            type="button"
            role="tab"
            aria-selected={photoActive}
            className={`s2-look-chip ${photoActive ? "s2-look-chip--active" : ""}`}
            onClick={() => (photo ? setLookId(PHOTO_LOOK.id) : fileInputRef.current?.click())}
          >
            <span className="s2-look-swatch s2-look-swatch--photo">📷</span>
            <span className="s2-look-label">Фото</span>
          </button>
        </div>

        <div className="s2-formats">
          {SHARE_FORMATS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`s2-format-tab ${item.id === formatId ? "s2-format-tab--active" : ""}`}
              onClick={() => selectFormat(item.id)}
              title={item.hint}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="s2-actions">
          <button type="button" className="s2-share-btn" onClick={onShare} disabled={busy || !fontsReady}>
            {busy ? "Готовим…" : systemShare ? "Поделиться" : "Скачать PNG"}
          </button>
          {systemShare ? (
            <button type="button" className="s2-secondary-btn" onClick={onDownload} disabled={busy || !fontsReady}>
              Скачать
            </button>
          ) : null}
          <button
            type="button"
            className="s2-secondary-btn"
            aria-expanded={customizeOpen}
            onClick={() => {
              setCustomizeOpen((open) => !open);
              if (!customizeOpen) {
                trackShareCustomize("open");
              }
            }}
          >
            Настроить
          </button>
        </div>

        {customizeOpen ? (
          <div className="s2-customize">
            <div className="s2-customize-title">Показатели ({activeMetricIds.length} из {limit})</div>
            <div className="s2-metric-toggles">
              {subject.data.metrics.map((metric) => {
                const active = activeMetricIds.includes(metric.id);
                return (
                  <button
                    key={metric.id}
                    type="button"
                    className={`s2-metric-toggle ${active ? "s2-metric-toggle--active" : ""}`}
                    aria-pressed={active}
                    onClick={() => toggleMetric(metric.id)}
                  >
                    <span className="s2-metric-toggle-value">{metric.value}</span>
                    <span className="s2-metric-toggle-label">{metric.label}</span>
                  </button>
                );
              })}
            </div>
            <div className="s2-customize-row">
              <button type="button" className="s2-secondary-btn" onClick={() => fileInputRef.current?.click()}>
                {photo ? "Заменить фото" : "Добавить своё фото"}
              </button>
              {photo ? (
                <button type="button" className="s2-secondary-btn" onClick={removePhoto}>
                  Убрать фото
                </button>
              ) : null}
            </div>
          </div>
        ) : null}

        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          hidden
          onChange={(event) => {
            void onPickPhoto(event.target.files?.[0]);
            event.target.value = "";
          }}
        />
      </div>

      {/* Экспортная сцена: карточка в нативном размере, за пределами экрана. */}
      <div className="s2-export-stage" aria-hidden="true">
        <div ref={exportRef}>{fontsReady ? <ShareCardView {...cardProps} /> : null}</div>
      </div>
    </div>
  );

  return createPortal(body, document.body);
}
