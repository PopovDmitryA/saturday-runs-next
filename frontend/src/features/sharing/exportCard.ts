// Экспорт карточки в PNG.
//
// Карточка — обычный React-компонент (дизайн версткой, дизайн-язык портала).
// Для экспорта тот же компонент рендерится оффскрином в НАТИВНОМ размере
// формата (1080×1920 и т.п.), html-to-image сериализует DOM в SVG
// foreignObject и растеризует. Шрифты вшиты (fonts.ts) и инлайнятся в PNG.
//
// Своё фото через этот конвейер не проходит: WebKit (то есть любой браузер на
// айфоне) растеризует foreignObject раньше, чем декодирует вложенные в него
// картинки, и ПЕРВЫЙ экспорт выходит без фото — второй уже с ним. Наружу это
// выглядело так: постер уехал в сторис без фотографии, а следующая отправка
// (в тот же телеграм) — уже с ней. Поэтому фото подкладываем сами: карточка
// снимается на прозрачном фоне, фото рисуется под ней на canvas.

import { toBlob } from "html-to-image";
import { PHOTO_BACKDROP_COLOR } from "./ShareCardView";

/** Своё фото под карточкой: адрес и место в координатах карточки. */
export type ExportBackdrop = {
  objectUrl: string;
  left: number;
  top: number;
  width: number;
  height: number;
};

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("Не удалось загрузить изображение постера"));
    image.src = src;
  });
}

function canvasToBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) {
        resolve(blob);
      } else {
        reject(new Error("Не удалось сформировать постер"));
      }
    }, "image/png");
  });
}

/**
 * Снимает PNG с оффскрин-узла карточки. Узел уже должен иметь нативные
 * пиксельные размеры формата; pixelRatio фиксируем в 1, чтобы retina-экраны
 * не раздували файл вдвое.
 *
 * fontEmbedCss ОБЯЗАТЕЛЕН: без него html-to-image сканирует все стили
 * документа в поисках @font-face (index.css — 12 тысяч строк) и экспорт
 * зависает; готовый CSS с data:-шрифтами (fonts.shareFontEmbedCss) это
 * отключает.
 *
 * backdrop — своё фото пользователя: карточку в этом случае снимаем прозрачной
 * и склеиваем с фото на canvas (см. шапку файла).
 */
export async function exportCardToPng(
  node: HTMLElement,
  width: number,
  height: number,
  fontEmbedCss: string,
  backdrop?: ExportBackdrop,
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
  if (!backdrop) {
    return blob;
  }

  const cardUrl = URL.createObjectURL(blob);
  try {
    const [card, photo] = await Promise.all([loadImage(cardUrl), loadImage(backdrop.objectUrl)]);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      throw new Error("Не удалось сформировать постер");
    }
    // Подложка видна там, где фото сдвинули или ужали от края карточки.
    ctx.fillStyle = PHOTO_BACKDROP_COLOR;
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(photo, backdrop.left, backdrop.top, backdrop.width, backdrop.height);
    ctx.drawImage(card, 0, 0, width, height);
    return await canvasToBlob(canvas);
  } finally {
    URL.revokeObjectURL(cardUrl);
  }
}
