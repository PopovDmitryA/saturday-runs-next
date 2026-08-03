// Экспорт карточки в PNG.
//
// Карточка — обычный React-компонент (дизайн версткой, дизайн-язык портала).
// Для экспорта тот же компонент рендерится оффскрином в НАТИВНОМ размере
// формата (1080×1920 и т.п.), html-to-image сериализует DOM в SVG
// foreignObject и растеризует. Шрифты вшиты (fonts.ts) и инлайнятся в PNG.

import { toBlob } from "html-to-image";

/**
 * Снимает PNG с оффскрин-узла карточки. Узел уже должен иметь нативные
 * пиксельные размеры формата; pixelRatio фиксируем в 1, чтобы retina-экраны
 * не раздували файл вдвое.
 */
export async function exportCardToPng(node: HTMLElement, width: number, height: number): Promise<Blob> {
  const blob = await toBlob(node, {
    width,
    height,
    pixelRatio: 1,
    cacheBust: true,
    // Стили карточки самодостаточны (инлайн + .s2-классы); тащить весь
    // 200-килобайтный index.css в SVG не нужно.
    skipFonts: false,
  });
  if (!blob) {
    throw new Error("Не удалось сформировать постер");
  }
  return blob;
}
