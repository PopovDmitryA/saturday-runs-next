// Единый рендерер постера.
//
// Карточка всегда верстается в НАТИВНОМ размере формата (1080×1920 и т.п.)
// с корневым font-size 40px; вся внутренняя типографика — в em. Превью
// масштабируется CSS-трансформом снаружи (SharePreview), экспорт снимает
// узел как есть — превью и PNG совпадают пиксель в пиксель.

import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { fitText, type FitBox } from "./fitText";
import { shareFontFamily, type ShareFontId } from "./fonts";
import {
  DEFAULT_PHOTO_TRANSFORM,
  type ShareLook,
  type SharePhoto,
  type ShareTone,
} from "./looks";
import type { ShareCardData, ShareFormat, ShareFormatId, ShareMetric, ShareNameList } from "./types";

/** Базовый кегль в нативном размере: 1em = 40px. */
const BASE_FONT_PX = 40;

/** Начертание героя (.s2-hero-value) — им же меряем строки при подборе кегля. */
const BOLD_WEIGHT = 800;

/**
 * Коробка под значение героя в каждом формате. Ширина выведена из паддингов
 * .s2-content, высота — из того, что остаётся в теле карточки под шапкой,
 * плашкой, подписью героя и бренд-футером (см. .s2-card--* в index.css).
 * В широком формате герой живёт в левой колонке грида 1fr / 1.25fr с зазором
 * 1.1em — отсюда 464px, вдвое меньше, чем в остальных форматах.
 *
 * Первый кегль лесенки — «как задумано»: короткие герои (число, время)
 * выглядят ровно так же, как до подбора.
 */
const HERO_BOXES: Record<ShareFormatId, FitBox> = {
  // 1080 − 2×1.6em паддинга.
  story: { maxWidthPx: 952, maxHeightPx: 470, maxLines: 3, sizesEm: [4.2, 3.4, 2.8, 2.3, 1.9, 1.55, 1.3] },
  // 1080 − 2×1.4em паддинга; по высоте между квадратом и сториз.
  feed: { maxWidthPx: 968, maxHeightPx: 400, maxLines: 3, sizesEm: [3.6, 3, 2.5, 2.1, 1.75, 1.45, 1.2] },
  // 1080 − 2×1.4em паддинга.
  square: { maxWidthPx: 968, maxHeightPx: 330, maxLines: 3, sizesEm: [3, 2.5, 2.1, 1.75, 1.45, 1.2] },
  // (1200 − 2×1.4em паддинга − 1.1em зазора) / 2.25 — левая колонка грида.
  wide: { maxWidthPx: 464, maxHeightPx: 240, maxLines: 3, sizesEm: [2.9, 2.4, 2, 1.7, 1.4, 1.15] },
};

/**
 * Сколько метрик-плиток влезает: формат × есть ли герой.
 * Значения выверены глазами по нативному размеру каждого формата — плитки
 * заполняют сетку без переполнения, но и без пустот.
 */
export function metricLimit(format: ShareFormat, data: ShareCardData): number {
  const hasHero = Boolean(data.hero);
  // Именные списки забирают место у плиток: под ними остаётся один-два ряда.
  const hasLists = (data.lists?.length ?? 0) > 0;
  if (format.id === "story") {
    // Вертикали много: 3 ряда по 2 плитки под героем, 4 ряда без него.
    // Со списками — 6: три списка по четыре строки под тремя рядами плиток
    // ещё оставляют воздух до бренд-футера (проверено на сводном посте).
    return hasLists ? 6 : hasHero ? 6 : 8;
  }
  if (format.id === "feed") {
    // 1080×1350: три ряда по две плитки под героем, четыре без него.
    return hasLists ? 4 : hasHero ? 6 : 8;
  }
  if (format.id === "square") {
    return hasLists ? 2 : hasHero ? 4 : 6;
  }
  // Широкий (1200×630): два ряда по три плитки. Ряд третьим не делаем — он
  // наезжает на бренд-футер, а вот третья колонка по ширине помещается.
  // Со списками — один ряд: списки ложатся под грид на всю ширину.
  return hasLists ? 3 : 6;
}

/**
 * Именные списки печатаются ЦЕЛИКОМ — ни одного «и ещё N» (правило Дмитрия
 * 07.09.2026: постер «спасибо волонтёрам» с недостающим именем — обида, и
 * такой постер публиковать не станут). Место под людей карточка находит
 * лесенкой: сначала мельче кегль плашек-имён, затем — «именной режим»: плитки
 * цифр убираются, герой сжимается в строку, весь корпус отдаётся именам.
 * Ступень выбирается измерением: пока корпус переполнен — шаг вниз.
 */
type NameStage = { tiles: boolean; sizeEm: number };

function stages(withTiles: number[], namesOnly: number[]): NameStage[] {
  return [
    ...withTiles.map((sizeEm) => ({ tiles: true, sizeEm })),
    ...namesOnly.map((sizeEm) => ({ tiles: false, sizeEm })),
  ];
}

// Именной режим начинается с кегля КРУПНЕЕ, чем был при плитках: без плиток
// места вдвое больше, и пятнадцать ролей волонтёров в ленте иначе оставляли
// пустой низ карточки.
// Нижние ступени — страховка под сотни имён (Томск: 224 новичка на одном
// старте): постер обязан вместить всех, пусть и мелко.
const NAME_STAGES: Record<ShareFormatId, NameStage[]> = {
  story: stages([0.55, 0.5, 0.45], [0.75, 0.65, 0.55, 0.5, 0.45, 0.4, 0.36, 0.32, 0.29, 0.26, 0.23, 0.2]),
  feed: stages([0.55, 0.5, 0.45], [0.7, 0.62, 0.55, 0.5, 0.45, 0.4, 0.36, 0.32, 0.29, 0.26, 0.23, 0.2]),
  square: stages([0.5, 0.45, 0.4], [0.6, 0.55, 0.5, 0.45, 0.4, 0.36, 0.32, 0.29, 0.26, 0.23, 0.2, 0.18]),
  wide: stages([0.42, 0.38, 0.34], [0.44, 0.4, 0.36, 0.33, 0.3, 0.27, 0.24, 0.22, 0.2, 0.18]),
};

/**
 * Ступень лесенки под данные и формат. Подбирается по факту: после каждого
 * рендера меряем корпус карточки, при переполнении опускаемся на ступень.
 * Экспортная копия карточки рендерится тем же компонентом и сходится к той
 * же ступени — превью и PNG совпадают.
 */
function useNameStage(
  bodyRef: { current: HTMLDivElement | null },
  format: ShareFormat,
  data: ShareCardData,
  metricsCount: number,
): NameStage | null {
  const ladder = NAME_STAGES[format.id];
  const hasLists = (data.lists ?? []).some((list) => list.items.length > 0);
  // Смена данных или формата начинает подбор заново — с самого крупного.
  const fitKey = hasLists
    ? `${format.id}|${metricsCount}|${(data.lists ?? []).map((list) => `${list.title}:${list.items.join(",")}`).join(";")}`
    : "";
  const [stage, setStage] = useState(0);
  const lastKey = useRef(fitKey);

  useLayoutEffect(() => {
    if (!hasLists) {
      return;
    }
    if (lastKey.current !== fitKey) {
      lastKey.current = fitKey;
      if (stage !== 0) {
        setStage(0);
        return;
      }
    }
    const body = bodyRef.current;
    if (!body) {
      return;
    }
    if (body.scrollHeight > body.clientHeight + 1 && stage < ladder.length - 1) {
      setStage(stage + 1);
    }
  });

  return hasLists ? ladder[Math.min(stage, ladder.length - 1)] : null;
}

/**
 * Цвета блоков: у каждого списка свой оттенок — заголовок-плашка сплошным
 * цветом, плашки имён его же тонированной подложкой. Без этого «первый
 * финиш», «первое волонтёрство» и «впервые у нас» сливались в одно поле
 * фамилий (Дмитрий, Томск 07.09.2026). Оттенки подобраны так, чтобы
 * читаться и на тёмных луках, и на светлом, и поверх своего фото.
 */
/** До скольких блоков плашки заголовков сплошные и яркие. */
const COLORFUL_LISTS_MAX = 4;

/**
 * Оттенки блоков. `solid`/`tint`/`ink` — яркий вариант для 2–4 смысловых
 * блоков (новички, юбилеи). `soft`/`softTint` — мягкий, полупрозрачный, для
 * длинных списков вроде 14 ролей волонтёров: цвет остаётся ориентиром, но не
 * бросается в глаза (правки Дмитрия 07.09.2026: сначала «вырвиглазно»,
 * потом «не хватает цветов»).
 */
const LIST_ACCENTS: { solid: string; tint: string; ink: string; soft: string; softTint: string }[] = [
  { solid: "#fbbf24", tint: "rgba(251, 191, 36, 0.24)", ink: "#451a03", soft: "rgba(251, 191, 36, 0.34)", softTint: "rgba(251, 191, 36, 0.13)" },
  { solid: "#38bdf8", tint: "rgba(56, 189, 248, 0.24)", ink: "#082f49", soft: "rgba(56, 189, 248, 0.34)", softTint: "rgba(56, 189, 248, 0.13)" },
  { solid: "#34d399", tint: "rgba(52, 211, 153, 0.24)", ink: "#022c22", soft: "rgba(52, 211, 153, 0.34)", softTint: "rgba(52, 211, 153, 0.13)" },
  { solid: "#f472b6", tint: "rgba(244, 114, 182, 0.24)", ink: "#500724", soft: "rgba(244, 114, 182, 0.34)", softTint: "rgba(244, 114, 182, 0.13)" },
  { solid: "#a78bfa", tint: "rgba(167, 139, 250, 0.26)", ink: "#2e1065", soft: "rgba(167, 139, 250, 0.36)", softTint: "rgba(167, 139, 250, 0.14)" },
  { solid: "#fb923c", tint: "rgba(251, 146, 60, 0.24)", ink: "#431407", soft: "rgba(251, 146, 60, 0.34)", softTint: "rgba(251, 146, 60, 0.13)" },
  { solid: "#a3e635", tint: "rgba(163, 230, 53, 0.22)", ink: "#1a2e05", soft: "rgba(163, 230, 53, 0.3)", softTint: "rgba(163, 230, 53, 0.12)" },
  { solid: "#f87171", tint: "rgba(248, 113, 113, 0.24)", ink: "#450a0a", soft: "rgba(248, 113, 113, 0.34)", softTint: "rgba(248, 113, 113, 0.13)" },
];

function NameLists({ lists }: { lists: ShareNameList[] }) {
  const visible = lists.filter((list) => list.items.length > 0);
  if (visible.length === 0) {
    return null;
  }
  return (
    <div className="s2-lists">
      {visible.map((list, index) => {
        // Немного блоков — яркие сплошные плашки; много (роли волонтёров) —
        // те же оттенки, но мягкие полупрозрачные, чтобы не рябило.
        const accent = LIST_ACCENTS[index % LIST_ACCENTS.length];
        const vivid = visible.length <= COLORFUL_LISTS_MAX;
        const listStyle = {
          "--s2-list-accent": vivid ? accent.solid : accent.soft,
          "--s2-list-tint": vivid ? accent.tint : accent.softTint,
          "--s2-list-ink": vivid ? accent.ink : "inherit",
        } as CSSProperties;
        return (
        <div key={list.title} className={`s2-list${vivid ? "" : " s2-list--soft"}`} style={listStyle}>
          <span className="s2-list-title">{list.title}</span>
          {list.items.map((item) => (
            <span key={item} className="s2-person">
              {item}
            </span>
          ))}
        </div>
        );
      })}
    </div>
  );
}

/**
 * Куда и в каком размере ложится своё фото внутри карточки. Одна и та же
 * геометрия нужна дважды: превью рисует фото тегом <img>, экспорт подкладывает
 * его под карточку на canvas (см. exportCard.ts) — считаем в одном месте,
 * чтобы картинки не разъехались.
 */
export function photoGeometry(
  photo: SharePhoto,
  format: ShareFormat,
): { left: number; top: number; width: number; height: number } {
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

/** Фон карточки под своим фото: видно там, где фото сдвинули с края. */
export const PHOTO_BACKDROP_COLOR = "#0f172a";

/** Подложка под фото по тону текста: тёмному тексту — светлая. */
export function photoBackdropColor(tone: ShareTone): string {
  return tone === "light" ? "#e2e8f0" : PHOTO_BACKDROP_COLOR;
}

/** Кегль и интерлиньяж героя, подобранные под колонку формата. */
function heroStyle(value: string, format: ShareFormat, font: ShareFontId): CSSProperties {
  const fit = fitText(value, HERO_BOXES[format.id], {
    basePx: BASE_FONT_PX,
    fontFamily: shareFontFamily(font),
    fontWeight: BOLD_WEIGHT,
  });
  return { fontSize: `${fit.sizeEm}em`, lineHeight: fit.lineHeight };
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
          <div className={`s2-tile-label ${metric.keepLabelCase ? "s2-tile-label--as-is" : ""}`}>
            {metric.label}
          </div>
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
  photoDrawnByExporter = false,
  photoTone = "dark",
}: {
  data: ShareCardData;
  format: ShareFormat;
  look: ShareLook;
  photo: SharePhoto | null;
  font: ShareFontId;
  /**
   * Тон поверх своего фото: dark — светлый текст и тёмная вуаль (по
   * умолчанию), light — тёмный текст и молочная вуаль для тёмных кадров.
   */
  photoTone?: ShareTone;
  /** Настроенный пользователем набор метрик; по умолчанию — приоритет данных. */
  visibleMetricIds?: string[];
  /**
   * Экспортный режим: фото подложит canvas, а карточка рисуется поверх него на
   * прозрачном фоне. Так своё фото вообще не попадает в SVG-снимок — обход
   * бага WebKit, из-за которого первый экспорт на айфоне выходил без фото.
   */
  photoDrawnByExporter?: boolean;
}) {
  const tone: ShareTone = photo ? photoTone : look.tone;
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
    background: photo
      ? photoDrawnByExporter
        ? "transparent"
        : photoBackdropColor(tone)
      : look.background,
    "--s2-tile-bg": photo
      ? tone === "light"
        ? "rgba(255, 255, 255, 0.62)"
        : "rgba(15, 23, 42, 0.45)"
      : look.tileBackground,
    "--s2-accent": look.accent,
    "--s2-accent-text": look.accentText,
  } as CSSProperties;

  // Именные списки: ступень лесенки (кегль имён, показывать ли плитки).
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const nameStage = useNameStage(bodyRef, format, data, metrics.length);
  const withLists = nameStage !== null;
  // Именной режим: плитки убраны, герой в строку, корпус целиком под имена.
  const namesOnly = withLists && !nameStage.tiles;
  const modeClass = `${withLists ? " s2-card--with-lists" : ""}${namesOnly ? " s2-card--names" : ""}`;
  if (withLists) {
    (rootStyle as Record<string, unknown>)["--s2-names-size"] = `${nameStage.sizeEm}em`;
  }

  return (
    <div
      className={`s2-card s2-card--${format.id} s2-tone-${tone}${modeClass}`}
      style={rootStyle}
      data-name-size={withLists ? nameStage.sizeEm : undefined}
    >
      {photo ? (
        <>
          {photoDrawnByExporter ? null : (
            <img className="s2-photo" src={photo.objectUrl} alt="" style={photoGeometry(photo, format)} />
          )}
          <div className={`s2-photo-overlay${tone === "light" ? " s2-photo-overlay--light" : ""}`} />
        </>
      ) : null}
      <div className="s2-content">
        <div className="s2-head">
          <div className="s2-name">{data.title}</div>
          {data.subtitle ? <div className="s2-sub">{data.subtitle}</div> : null}
        </div>
        <div className="s2-body" ref={bodyRef}>
          {namesOnly ? (
            // Именной режим: плашка и герой в одну строку — каждый
            // сэкономленный ряд достаётся именам.
            <div className="s2-topline">
              {data.hero ? (
                <div className="s2-hero s2-hero--compact">
                  <div className="s2-hero-value">{data.hero.value}</div>
                  {data.hero.caption ? <div className="s2-hero-caption">{data.hero.caption}</div> : null}
                </div>
              ) : null}
              {data.plate ? <div className="s2-plate">{data.plate}</div> : null}
            </div>
          ) : (
            <>
              {data.plate ? <div className="s2-plate">{data.plate}</div> : null}
              {data.hero ? (
                <div className="s2-hero">
                  <div className="s2-hero-value" style={heroStyle(data.hero.value, format, font)}>
                    {data.hero.value}
                  </div>
                  {data.hero.caption ? <div className="s2-hero-caption">{data.hero.caption}</div> : null}
                </div>
              ) : null}
            </>
          )}
          {data.chip ? <div className="s2-chip">{data.chip}</div> : null}
          {data.progress ? (
            <div className="s2-progress">
              <div className="s2-progress-track">
                <div
                  className="s2-progress-fill"
                  style={{ width: `${Math.max(0, Math.min(100, data.progress.pct))}%` }}
                />
              </div>
              {data.progress.label ? (
                <div className="s2-progress-caption">{data.progress.label}</div>
              ) : null}
            </div>
          ) : null}
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
          {namesOnly ? null : <MetricTiles metrics={metrics} limit={metricLimit(format, data)} />}
          {data.lists ? <NameLists lists={data.lists} /> : null}
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
