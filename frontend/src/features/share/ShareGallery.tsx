import { useEffect, useMemo, useRef, useState } from "react";
import type { DashboardStats, RunItem } from "../../lib/api";
import { loadShareBackgroundImage, preloadShareBackgrounds } from "./shareBackgrounds";
import { downloadShareBlob, renderShareCard, shareCardToBlob } from "./shareCardRenderer";
import { getShareFormat } from "./shareConfig";
import { buildRandomShareExamples, type ShareGalleryExample } from "./shareExamples";

const STORY_FORMAT = getShareFormat("story");
const GALLERY_SCALE = 0.34;

type ShareGalleryProps = {
  stats: DashboardStats;
  runs: RunItem[];
  displayName: string;
  onCreate: () => void;
};

function safeFileNamePart(value: string): string {
  return value.replace(/[^\w\u0400-\u04FF.-]+/gu, "_").slice(0, 40);
}

function ShareGalleryCard({
  example,
  backgroundImage,
  displayName,
}: {
  example: ShareGalleryExample;
  backgroundImage: HTMLImageElement | null;
  displayName: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const exportCanvasRef = useRef<HTMLCanvasElement>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  useEffect(() => {
    if (!backgroundImage || !canvasRef.current) {
      return;
    }
    renderShareCard(
      canvasRef.current,
      {
        format: STORY_FORMAT,
        displayName: example.displayName,
        metrics: example.metrics,
        backgroundImage,
        textColor: example.textColor,
        summaryLine: example.summaryLine,
      },
      { scale: GALLERY_SCALE },
    );
  }, [backgroundImage, example]);

  const handleDownload = async () => {
    if (!backgroundImage || !exportCanvasRef.current) {
      return;
    }
    setDownloading(true);
    setDownloadError(null);
    try {
      renderShareCard(exportCanvasRef.current, {
        format: STORY_FORMAT,
        displayName: example.displayName,
        metrics: example.metrics,
        backgroundImage,
        textColor: example.textColor,
        summaryLine: example.summaryLine,
      });
      const blob = await shareCardToBlob(exportCanvasRef.current);
      const safeName = safeFileNamePart(displayName) || "stats";
      downloadShareBlob(
        blob,
        `share-story-${example.period}-${example.preset.id}-${safeName}.png`,
      );
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : "Не удалось скачать PNG");
    } finally {
      setDownloading(false);
    }
  };

  const hasMetrics = example.metrics.some((metric) => metric != null);
  const ready = Boolean(backgroundImage) && hasMetrics;

  return (
    <article className="share-gallery-card">
      <div className="share-gallery-preview-wrap" tabIndex={ready ? 0 : -1}>
        <canvas
          ref={canvasRef}
          className="share-gallery-canvas"
          style={{ aspectRatio: `${STORY_FORMAT.width} / ${STORY_FORMAT.height}` }}
        />
        <div className="share-gallery-overlay" aria-hidden={!ready}>
          <button
            type="button"
            className="share-gallery-download"
            disabled={!ready || downloading}
            onClick={() => void handleDownload()}
          >
            {downloading ? "Создание…" : "Скачать"}
          </button>
        </div>
      </div>
      {downloadError && <p className="error-text share-gallery-download-error">{downloadError}</p>}
      <canvas ref={exportCanvasRef} className="share-export-canvas" aria-hidden="true" />
    </article>
  );
}

export function ShareGallery({ stats, runs, displayName, onCreate }: ShareGalleryProps) {
  const currentYear = new Date().getFullYear();
  const examples = useMemo(
    () =>
      buildRandomShareExamples({
        stats,
        runs,
        displayName,
        currentYear,
      }),
    [stats, runs, displayName, currentYear],
  );
  const [images, setImages] = useState<Partial<Record<string, HTMLImageElement>>>({});

  useEffect(() => {
    preloadShareBackgrounds();
    let cancelled = false;

    const uniqueBackgrounds = [...new Map(examples.map((ex) => [ex.background.id, ex.background])).values()];

    void Promise.all(
      uniqueBackgrounds.map(async (background) => {
        const image = await loadShareBackgroundImage(background);
        return [background.id, image] as const;
      }),
    ).then((entries) => {
      if (cancelled) {
        return;
      }
      setImages(Object.fromEntries(entries));
    });

    return () => {
      cancelled = true;
    };
  }, [examples]);

  return (
    <div className="share-gallery">
      <div className="share-gallery-header">
        <div className="share-gallery-header-text">
          <h2 className="share-gallery-title">Делитесь статистикой с друзьями</h2>
          <p className="muted share-gallery-subtitle">
            Скачайте готовую карточку для Stories или соберите постер под себя.
          </p>
        </div>
        <button type="button" className="share-gallery-create" onClick={onCreate}>
          Создать свой постер
        </button>
      </div>

      <div className="share-gallery-grid">
        {examples.map((example) => (
          <ShareGalleryCard
            key={example.id}
            example={example}
            backgroundImage={images[example.background.id] ?? null}
            displayName={displayName}
          />
        ))}
      </div>
    </div>
  );
}
