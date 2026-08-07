// Единый рендерер постера.
//
// Карточка всегда верстается в НАТИВНОМ размере формата (1080×1920 и т.п.)
// с корневым font-size 40px; вся внутренняя типографика — в em. Превью
// масштабируется CSS-трансформом снаружи (SharePreview), экспорт снимает
// узел как есть — превью и PNG совпадают пиксель в пиксель.

import type { CSSProperties } from "react";
import { shareFontFamily, type ShareFontId } from "./fonts";
import {
  DEFAULT_PHOTO_TRANSFORM,
  type ShareLook,
  type SharePhoto,
} from "./looks";
import type { ShareCardData, ShareFormat, ShareMetric } from "./types";

/** Базовый кегль в нативном размере: 1em = 40px. */
const BASE_FONT_PX = 40;

/** Сколько метрик-плиток влезает: формат × есть ли герой. */
export function metricLimit(format: ShareFormat, data: ShareCardData): number {
  const hasHero = Boolean(data.hero);
  if (format.id === "story") {
    return hasHero ? 4 : 6;
  }
  if (format.id === "square") {
    return hasHero ? 3 : 4;
  }
  // Широкий: без героя места хватает на богатую сетку (визитка локации).
  return hasHero ? 3 : 6;
}

function photoStyle(photo: SharePhoto, format: ShareFormat): CSSProperties {
  const transform = photo.transforms[format.id] ?? DEFAULT_PHOTO_TRANSFORM;
  const cover = Math.max(format.width / photo.width, format.height / photo.height);
  const width = photo.width * cover * transform.scale;
  const height = photo.height * cover * transform.scale;
  return {
    width,
    height,
    left: format.width / 2 - width / 2 + transform.offsetX * format.width,
    top: format.height / 2 - height / 2 + transform.offsetY * format.height,
  };
}

function MetricTiles({ metrics, limit }: { metrics: ShareMetric[]; limit: number }) {
  const visible = metrics.slice(0, limit);
  if (visible.length === 0) {
    return null;
  }
  return (
    <div className="s2-metrics">
      {visible.map((metric) => (
        <div key={metric.id} className="s2-tile">
          <div className={`s2-tile-value ${metric.value.length > 4 ? "s2-tile-value--long" : ""}`}>
            {metric.value}
          </div>
          <div className="s2-tile-label">{metric.label}</div>
        </div>
      ))}
    </div>
  );
}

export function ShareCardView({
  data,
  format,
  look,
  photo,
  font,
  visibleMetricIds,
}: {
  data: ShareCardData;
  format: ShareFormat;
  look: ShareLook;
  photo: SharePhoto | null;
  font: ShareFontId;
  /** Настроенный пользователем набор метрик; по умолчанию — приоритет данных. */
  visibleMetricIds?: string[];
}) {
  const tone = photo ? "dark" : look.tone;
  const metrics = visibleMetricIds
    ? visibleMetricIds
        .map((id) => data.metrics.find((metric) => metric.id === id))
        .filter((metric): metric is ShareMetric => Boolean(metric))
    : data.metrics;

  const rootStyle = {
    width: format.width,
    height: format.height,
    fontSize: BASE_FONT_PX,
    fontFamily: shareFontFamily(font),
    background: photo ? "#0f172a" : look.background,
    "--s2-tile-bg": photo ? "rgba(15, 23, 42, 0.45)" : look.tileBackground,
    "--s2-accent": look.accent,
    "--s2-accent-text": look.accentText,
  } as CSSProperties;

  return (
    <div className={`s2-card s2-card--${format.id} s2-tone-${tone}`} style={rootStyle}>
      {photo ? (
        <>
          <img className="s2-photo" src={photo.objectUrl} alt="" style={photoStyle(photo, format)} />
          <div className="s2-photo-overlay" />
        </>
      ) : null}
      <div className="s2-content">
        <div className="s2-head">
          <div className="s2-name">{data.title}</div>
          {data.subtitle ? <div className="s2-sub">{data.subtitle}</div> : null}
        </div>
        <div className="s2-body">
          {data.plate ? <div className="s2-plate">{data.plate}</div> : null}
          {data.hero ? (
            <div className="s2-hero">
              <div className={`s2-hero-value ${data.hero.value.length > 4 ? "s2-hero-value--long" : ""}`}>
                {data.hero.value}
              </div>
              {data.hero.caption ? <div className="s2-hero-caption">{data.hero.caption}</div> : null}
            </div>
          ) : null}
          {data.chip ? <div className="s2-chip">{data.chip}</div> : null}
          {data.timeline && data.timeline.length > 0 ? (
            <div className="s2-timeline">
              {data.timeline.map((entry) => (
                <div key={`${entry.label}-${entry.period}`} className="s2-timeline-entry">
                  <span className={`s2-timeline-dot ${entry.current ? "s2-timeline-dot--current" : ""}`} />
                  <span className="s2-timeline-text">
                    <b>{entry.label}</b>
                    <span>{entry.period}</span>
                  </span>
                </div>
              ))}
            </div>
          ) : null}
          <MetricTiles metrics={metrics} limit={metricLimit(format, data)} />
          {data.heat && data.heat.length > 0 ? (
            <div className="s2-heat-wrap">
              <div className="s2-heat">
                {data.heat.map((on, index) => (
                  // Индекс — честный ключ: полоска статична и не пересортировывается.
                  // eslint-disable-next-line react/no-array-index-key
                  <i key={index} className={on ? "s2-heat-on" : ""} />
                ))}
              </div>
              <div className="s2-heat-caption">субботы</div>
            </div>
          ) : null}
          {data.fact ? <div className="s2-fact">{data.fact}</div> : null}
        </div>
        <div className="s2-brand">
          {/* Логотип как в шапке сайта: run5k + акцентный .run + линия-пульс. */}
          <div className="s2-brand-mark">
            <span className="s2-brand-logo">
              run5k<span className="s2-brand-tld">.run</span>
            </span>
            <svg className="s2-brand-pulse" viewBox="0 0 34 14" fill="none" aria-hidden="true">
              <polyline
                points="1,12 8,10 14,11 20,6 26,7 32,2"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <circle cx="32" cy="2" r="2.4" />
            </svg>
          </div>
          <div className="s2-brand-tag">статистика парковых пробежек</div>
        </div>
      </div>
    </div>
  );
}
