// Прикреплённые фото: сетка миниатюр, загрузка новых, удаление, просмотр в
// полный размер. Один компонент на двух потребителей (отзывы на локации — до 5,
// карточки бэклога — до 3): различаются только лимитом и колбэками загрузки.
//
// Компонент НЕ ходит в API сам — вызывающий отдаёт onUpload/onDelete. Так один
// и тот же UI работает и с /ratings, и с /backlog, у которых разные клиенты.

import { useRef, useState } from "react";

import { ImageLightbox } from "./ImageLightbox";
import "./PhotoAttachments.css";

export type AttachedPhoto = {
  id: string;
  url: string;
  width: number;
  height: number;
};

type Props = {
  photos: AttachedPhoto[];
  maxPhotos: number;
  // Чтение (без кнопок загрузки/удаления) — для чужих карточек и отзывов.
  readOnly?: boolean;
  onUpload?: (file: File) => Promise<AttachedPhoto>;
  onDelete?: (photoId: string) => Promise<void>;
  onChange?: (photos: AttachedPhoto[]) => void;
  label?: string;
};

const ACCEPT = "image/jpeg,image/png,image/webp";

export function PhotoAttachments({
  photos,
  maxPhotos,
  readOnly = false,
  onUpload,
  onDelete,
  onChange,
  label = "Фото",
}: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<AttachedPhoto | null>(null);

  const canAdd = !readOnly && onUpload != null && photos.length < maxPhotos;

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0 || onUpload == null) return;
    setError(null);
    setBusy(true);
    // Файлы грузим по одному и последовательно: лимит считает сервер, и при
    // параллельной отправке последние запросы всё равно упёрлись бы в отказ.
    const added: AttachedPhoto[] = [];
    try {
      for (const file of Array.from(files)) {
        if (photos.length + added.length >= maxPhotos) break;
        added.push(await onUpload(file));
      }
      if (added.length > 0) onChange?.([...photos, ...added]);
    } catch (err) {
      if (added.length > 0) onChange?.([...photos, ...added]);
      setError(err instanceof Error ? err.message : "Не удалось загрузить фото");
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function handleDelete(photo: AttachedPhoto) {
    if (onDelete == null) return;
    setError(null);
    setBusy(true);
    try {
      await onDelete(photo.id);
      onChange?.(photos.filter((item) => item.id !== photo.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить фото");
    } finally {
      setBusy(false);
    }
  }

  if (readOnly && photos.length === 0) return null;

  return (
    <div className="photo-attachments">
      {!readOnly && (
        <div className="photo-attachments__head">
          <span className="photo-attachments__label">{label}</span>
          <span className="photo-attachments__counter">
            {photos.length} из {maxPhotos}
          </span>
        </div>
      )}

      <div className="photo-attachments__grid">
        {photos.map((photo) => (
          <div className="photo-attachments__item" key={photo.id}>
            <button
              type="button"
              className="photo-attachments__thumb"
              onClick={() => setLightbox(photo)}
              aria-label="Открыть фото"
            >
              <img src={photo.url} alt="" loading="lazy" />
            </button>
            {!readOnly && onDelete != null && (
              <button
                type="button"
                className="photo-attachments__remove"
                onClick={() => void handleDelete(photo)}
                disabled={busy}
                aria-label="Удалить фото"
              >
                ×
              </button>
            )}
          </div>
        ))}

        {canAdd && (
          <button
            type="button"
            className="photo-attachments__add"
            onClick={() => inputRef.current?.click()}
            disabled={busy}
          >
            <span className="photo-attachments__add-icon">＋</span>
            <span className="photo-attachments__add-text">
              {busy ? "Загрузка…" : "Добавить"}
            </span>
          </button>
        )}
      </div>

      {canAdd && (
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          multiple
          hidden
          onChange={(event) => void handleFiles(event.target.files)}
        />
      )}

      {error != null && <p className="photo-attachments__error">{error}</p>}

      {lightbox != null && (
        <ImageLightbox src={lightbox.url} onClose={() => setLightbox(null)} />
      )}
    </div>
  );
}
