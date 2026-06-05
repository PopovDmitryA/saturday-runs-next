import type { ShareFormat, ShareTextColor, ShareTextTreatment } from "./shareConfig";
import { getShareTextColorDef, SHARE_PROJECT_NAME, SHARE_TAGLINE, SHARE_WATERMARK } from "./shareConfig";
import type { ShareMetricRow } from "./shareMetrics";

export type ShareCardInput = {
  format: ShareFormat;
  displayName: string;
  metrics: (ShareMetricRow | null)[];
  backgroundImage: HTMLImageElement | null;
  textColor: ShareTextColor;
  summaryLine?: string | null;
};

const FONT = "system-ui, -apple-system, BlinkMacSystemFont, sans-serif";
const FOOTER_RATIO = 0.15;
const HEADER_RATIO = 0.2;
const MAX_METRICS = 3;

type TextPalette = {
  primary: string;
  secondary: string;
  divider: string;
  treatment: ShareTextTreatment;
};

function textPalette(textColor: ShareTextColor): TextPalette {
  const def = getShareTextColorDef(textColor);
  return {
    primary: def.primary,
    secondary: def.secondary,
    divider: def.divider,
    treatment: def.treatment,
  };
}

function wrapText(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string[] {
  const words = text.split(/\s+/);
  const lines: string[] = [];
  let line = "";
  for (const word of words) {
    const test = line ? `${line} ${word}` : word;
    if (ctx.measureText(test).width <= maxWidth) {
      line = test;
    } else {
      if (line) {
        lines.push(line);
      }
      line = word;
    }
  }
  if (line) {
    lines.push(line);
  }
  return lines.length > 0 ? lines : [text];
}

function fitFontSize(
  ctx: CanvasRenderingContext2D,
  text: string,
  maxWidth: number,
  maxHeight: number,
  weight: string,
  startSize: number,
  minSize: number,
): number {
  let size = startSize;
  while (size >= minSize) {
    ctx.font = `${weight} ${size}px ${FONT}`;
    const width = ctx.measureText(text).width;
    if (width <= maxWidth && size <= maxHeight) {
      return size;
    }
    size -= 2;
  }
  return minSize;
}

function fitMultilineFontSize(
  ctx: CanvasRenderingContext2D,
  lines: string[],
  maxWidth: number,
  maxHeight: number,
  weight: string,
  startSize: number,
  minSize: number,
  lineHeight: number,
): number {
  let size = startSize;
  while (size >= minSize) {
    ctx.font = `${weight} ${size}px ${FONT}`;
    const widest = Math.max(...lines.map((line) => ctx.measureText(line).width));
    const totalH = lines.length * size * lineHeight;
    if (widest <= maxWidth && totalH <= maxHeight) {
      return size;
    }
    size -= 2;
  }
  return minSize;
}

function drawRoundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  const radius = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + w, y, x + w, y + h, radius);
  ctx.arcTo(x + w, y + h, x, y + h, radius);
  ctx.arcTo(x, y + h, x, y, radius);
  ctx.arcTo(x, y, x + w, y, radius);
  ctx.closePath();
}

function drawPhotoBackground(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  image: HTMLImageElement | null,
) {
  if (image) {
    const scale = Math.max(w / image.width, h / image.height);
    const drawW = image.width * scale;
    const drawH = image.height * scale;
    ctx.drawImage(image, (w - drawW) / 2, (h - drawH) / 2, drawW, drawH);
  } else {
    ctx.fillStyle = "#334155";
    ctx.fillRect(0, 0, w, h);
  }
}

function drawEdgeGradients(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  headerH: number,
  treatment: ShareTextTreatment,
) {
  const topEnd = headerH + h * 0.04;
  const topGradient = ctx.createLinearGradient(0, 0, 0, topEnd);
  if (treatment === "light") {
    topGradient.addColorStop(0, "rgba(0, 0, 0, 0.88)");
    topGradient.addColorStop(0.45, "rgba(0, 0, 0, 0.55)");
    topGradient.addColorStop(1, "rgba(0, 0, 0, 0)");
  } else {
    topGradient.addColorStop(0, "rgba(255, 255, 255, 0.78)");
    topGradient.addColorStop(0.45, "rgba(255, 255, 255, 0.38)");
    topGradient.addColorStop(1, "rgba(255, 255, 255, 0)");
  }
  ctx.fillStyle = topGradient;
  ctx.fillRect(0, 0, w, topEnd);
}

function drawTextWithShadow(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  palette: TextPalette,
  align: CanvasTextAlign = "center",
  color = palette.primary,
) {
  ctx.textAlign = align;
  ctx.fillStyle = color;

  if (palette.treatment === "light") {
    ctx.shadowColor = "rgba(0, 0, 0, 0.62)";
    ctx.shadowBlur = 10;
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = 2;
    ctx.fillText(text, x, y);
  } else {
    ctx.shadowColor = "rgba(0, 0, 0, 0.2)";
    ctx.shadowBlur = 1;
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = 1;
    ctx.fillText(text, x, y);
  }

  ctx.shadowColor = "transparent";
  ctx.shadowBlur = 0;
  ctx.shadowOffsetX = 0;
  ctx.shadowOffsetY = 0;
}

function drawHeader(
  ctx: CanvasRenderingContext2D,
  w: number,
  headerTop: number,
  headerH: number,
  pad: number,
  displayName: string,
  palette: TextPalette,
  summaryLine: string | null,
) {
  const innerW = w - pad * 2;
  const nameLines = wrapText(ctx, displayName, innerW).slice(0, 2);
  const nameSize = fitMultilineFontSize(ctx, nameLines, innerW, headerH * 0.52, "700", headerH * 0.22, 28, 1.08);
  ctx.font = `700 ${nameSize}px ${FONT}`;
  const nameBlockH = nameLines.length * nameSize * 1.08;
  let y = headerTop + headerH * 0.28 + nameSize;

  for (const line of nameLines) {
    drawTextWithShadow(ctx, line, w / 2, y, palette);
    y += nameSize * 1.08;
  }

  const tagText = summaryLine ?? SHARE_TAGLINE;
  const tagSize = fitFontSize(ctx, tagText, innerW, headerH * 0.22, "500", nameSize * 0.42, 16);
  ctx.font = `500 ${tagSize}px ${FONT}`;
  const tagY = headerTop + headerH * 0.28 + nameBlockH + tagSize * 1.35;
  drawTextWithShadow(ctx, tagText, w / 2, tagY, palette, "center", palette.secondary);
}

function drawFooter(ctx: CanvasRenderingContext2D, w: number, h: number, footerH: number, pad: number) {
  const innerW = w - pad * 2;
  const badgePadX = Math.round(w * 0.06);
  const badgePadY = Math.round(footerH * 0.14);

  const wmSize = fitFontSize(ctx, SHARE_WATERMARK, innerW - badgePadX * 2, footerH * 0.32, "800", footerH * 0.26, 22);
  ctx.font = `800 ${wmSize}px ${FONT}`;
  const wmWidth = ctx.measureText(SHARE_WATERMARK).width;

  const tagSize = fitFontSize(
    ctx,
    SHARE_PROJECT_NAME,
    innerW - badgePadX * 2,
    footerH * 0.22,
    "500",
    footerH * 0.13,
    12,
  );
  ctx.font = `500 ${tagSize}px ${FONT}`;
  const tagWidth = ctx.measureText(SHARE_PROJECT_NAME).width;

  const badgeW = Math.min(innerW, Math.max(wmWidth, tagWidth) + badgePadX * 2);
  const badgeH = wmSize + tagSize * 1.35 + badgePadY * 2;
  const badgeX = (w - badgeW) / 2;
  const badgeY = h - footerH + (footerH - badgeH) / 2;

  ctx.save();
  ctx.shadowColor = "rgba(0, 0, 0, 0.45)";
  ctx.shadowBlur = 20;
  ctx.shadowOffsetY = 4;
  drawRoundedRect(ctx, badgeX, badgeY, badgeW, badgeH, 18);
  ctx.fillStyle = "rgba(15, 23, 42, 0.72)";
  ctx.fill();
  ctx.restore();

  drawRoundedRect(ctx, badgeX, badgeY, badgeW, badgeH, 18);
  ctx.strokeStyle = "rgba(255, 255, 255, 0.38)";
  ctx.lineWidth = 2;
  ctx.stroke();

  const accentBarH = 4;
  drawRoundedRect(ctx, badgeX + 2, badgeY + 2, badgeW - 4, accentBarH, 2);
  ctx.fillStyle = "#2563eb";
  ctx.fill();

  const badgePalette = textPalette("white");
  ctx.font = `800 ${wmSize}px ${FONT}`;
  ctx.letterSpacing = "0.04em";
  const wmY = badgeY + badgePadY + wmSize * 0.95;
  drawTextWithShadow(ctx, SHARE_WATERMARK, w / 2, wmY, badgePalette);
  ctx.letterSpacing = "0px";

  ctx.font = `500 ${tagSize}px ${FONT}`;
  drawTextWithShadow(
    ctx,
    SHARE_PROJECT_NAME,
    w / 2,
    wmY + tagSize * 1.35,
    badgePalette,
    "center",
    "rgba(226, 232, 240, 0.95)",
  );
}

function drawMetricBlock(
  ctx: CanvasRenderingContext2D,
  metric: ShareMetricRow,
  x: number,
  y: number,
  zoneW: number,
  zoneH: number,
  palette: TextPalette,
) {
  const padX = zoneW * 0.08;
  const maxW = zoneW - padX * 2;

  ctx.font = `500 ${zoneH * 0.14}px ${FONT}`;
  const labelLines = wrapText(ctx, metric.label, maxW).slice(0, 2);
  const labelSize = fitMultilineFontSize(
    ctx,
    labelLines,
    maxW,
    zoneH * 0.22,
    "500",
    zoneH * 0.14,
    14,
    1.15,
  );

  ctx.font = `700 ${zoneH * 0.42}px ${FONT}`;
  const valueLines = wrapText(ctx, metric.value, maxW).slice(0, 2);
  const valueSize = fitMultilineFontSize(
    ctx,
    valueLines,
    maxW,
    zoneH * 0.55,
    "700",
    zoneH * 0.42,
    24,
    1.05,
  );

  const labelBlockH = labelLines.length * labelSize * 1.15;
  const gap = zoneH * 0.04;
  const blockH = labelBlockH + gap + valueLines.length * valueSize * 1.05;
  const startY = y + (zoneH - blockH) / 2;

  ctx.font = `500 ${labelSize}px ${FONT}`;
  let labelY = startY + labelSize;
  for (const line of labelLines) {
    drawTextWithShadow(ctx, line, x + zoneW / 2, labelY, palette, "center", palette.secondary);
    labelY += labelSize * 1.15;
  }
  ctx.font = `700 ${valueSize}px ${FONT}`;
  let valueY = startY + labelBlockH + gap + valueSize;
  for (const line of valueLines) {
    drawTextWithShadow(ctx, line, x + zoneW / 2, valueY, palette);
    valueY += valueSize * 1.05;
  }
}

function drawMetricZones(
  ctx: CanvasRenderingContext2D,
  w: number,
  middleTop: number,
  middleH: number,
  metrics: (ShareMetricRow | null)[],
  horizontal: boolean,
  palette: TextPalette,
) {
  const count = MAX_METRICS;
  const shown = metrics.slice(0, MAX_METRICS);
  while (shown.length < count) {
    shown.push(null);
  }

  for (let i = 0; i < count; i += 1) {
    let x: number;
    let y: number;
    let zoneW: number;
    let zoneH: number;

    if (horizontal) {
      zoneW = w / count;
      zoneH = middleH;
      x = i * zoneW;
      y = middleTop;
    } else {
      zoneW = w;
      zoneH = middleH / count;
      x = 0;
      y = middleTop + i * zoneH;
    }

    if (i > 0) {
      ctx.strokeStyle = palette.divider;
      ctx.lineWidth = 1;
      ctx.beginPath();
      if (horizontal) {
        ctx.moveTo(x, middleTop);
        ctx.lineTo(x, middleTop + middleH);
      } else {
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
      }
      ctx.stroke();
    }

    const metric = shown[i];
    if (metric) {
      drawMetricBlock(ctx, metric, x, y, zoneW, zoneH, palette);
    }
  }
}

export function renderShareCard(
  canvas: HTMLCanvasElement,
  input: ShareCardInput,
  options?: { scale?: number },
): void {
  const { format, displayName, metrics, backgroundImage, textColor, summaryLine = null } = input;
  const scale = options?.scale ?? 1;
  const w = format.width;
  const h = format.height;
  canvas.width = Math.round(w * scale);
  canvas.height = Math.round(h * scale);
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return;
  }

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  if (scale !== 1) {
    ctx.scale(scale, scale);
  }

  const palette = textPalette(textColor);
  const pad = Math.round(w * 0.06);
  const footerH = h * FOOTER_RATIO;
  const headerH = h * HEADER_RATIO;
  const middleTop = headerH;
  const middleH = h - headerH - footerH;
  const horizontal = format.id === "wide";

  drawPhotoBackground(ctx, w, h, backgroundImage);
  drawEdgeGradients(ctx, w, h, headerH, palette.treatment);
  drawHeader(ctx, w, 0, headerH, pad, displayName, palette, summaryLine);
  drawMetricZones(ctx, w, middleTop, middleH, metrics, horizontal, palette);
  drawFooter(ctx, w, h, footerH, pad);
}

export async function shareCardToBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob) {
          resolve(blob);
        } else {
          reject(new Error("Не удалось создать PNG"));
        }
      },
      "image/png",
      1,
    );
  });
}

export function downloadShareBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
