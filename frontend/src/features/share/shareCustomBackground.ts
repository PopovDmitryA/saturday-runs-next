export type CropAdjust = {
  scale: number;
  offsetX: number;
  offsetY: number;
};

export function loadImageFromFile(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const objectUrl = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error("Не удалось прочитать изображение"));
    };
    img.src = objectUrl;
  });
}

export function revokeImageObjectUrl(image: HTMLImageElement | null | undefined): void {
  if (image?.src.startsWith("blob:")) {
    URL.revokeObjectURL(image.src);
  }
}

export function imageElementFromCanvas(canvas: HTMLCanvasElement): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("Не удалось подготовить фон"));
    img.src = canvas.toDataURL("image/jpeg", 0.92);
  });
}

export function cropImageToFormat(
  source: HTMLImageElement,
  width: number,
  height: number,
  adjust: CropAdjust,
): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return canvas;
  }

  const baseScale = Math.max(width / source.width, height / source.height);
  const scale = baseScale * Math.max(1, adjust.scale);
  const drawW = source.width * scale;
  const drawH = source.height * scale;
  const x = (width - drawW) / 2 + adjust.offsetX;
  const y = (height - drawH) / 2 + adjust.offsetY;

  ctx.drawImage(source, x, y, drawW, drawH);
  return canvas;
}

export function defaultCropAdjust(): CropAdjust {
  return { scale: 1, offsetX: 0, offsetY: 0 };
}
