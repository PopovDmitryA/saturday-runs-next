// Выходы постера наружу: системный share sheet или скачивание.
//
// navigator.share({files}) работает в iOS Safari и Android Chrome — картинка
// уходит прямо в Telegram/Instagram/WhatsApp без сохранения в галерею.
// На десктопе и в браузерах без share-файлов кнопка деградирует в скачивание.

export type ShareOutcome = "system" | "download" | "cancelled" | "failed";

function makeFile(blob: Blob, fileName: string): File {
  return new File([blob], `${fileName}.png`, { type: "image/png" });
}

/** Умеет ли браузер отдать PNG в системный share sheet. */
export function canShareFiles(): boolean {
  if (typeof navigator === "undefined" || typeof navigator.canShare !== "function") {
    return false;
  }
  try {
    const probe = new File([new Uint8Array(4)], "probe.png", { type: "image/png" });
    return navigator.canShare({ files: [probe] });
  } catch {
    return false;
  }
}

export function downloadBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${fileName}.png`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Отложенно: немедленный revoke обрывает скачивание в Safari.
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/**
 * Отдаёт постер в системный share sheet, если умеем, иначе скачивает.
 * «cancelled» (пользователь закрыл шит) — не успех и не ошибка: вызывающая
 * сторона не пишет share_success.
 */
export async function shareOrDownload(blob: Blob, fileName: string, title: string): Promise<ShareOutcome> {
  if (canShareFiles()) {
    try {
      await navigator.share({ files: [makeFile(blob, fileName)], title });
      return "system";
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return "cancelled";
      }
      // NotAllowedError и прочее — падаем в скачивание, постер важнее канала.
    }
  }
  try {
    downloadBlob(blob, fileName);
    return "download";
  } catch {
    return "failed";
  }
}
