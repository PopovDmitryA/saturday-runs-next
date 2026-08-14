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
 *
 * fontEmbedCss ОБЯЗАТЕЛЕН: без него html-to-image сканирует все стили
 * документа в поисках @font-face (index.css — 12 тысяч строк) и экспорт
 * зависает; готовый CSS с data:-шрифтами (fonts.shareFontEmbedCss) это
 * отключает.
 */
export async function exportCardToPng(
  node: HTMLElement,
  width: number,
  height: number,
  fontEmbedCss: string,
): Promise<Blob> {
  const blob = await toBlob(node, {
    width,
    height,
    pixelRatio: 1,
    fontEmbedCSS: fontEmbedCss,
  });
  if (!blob) {
    throw new Error("Не удалось сформировать постер");
  }
  return blob;
}
