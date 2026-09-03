// Прикреплённые фото: сетка миниатюр, выбор новых, удаление, просмотр в
// полный размер. Один компонент на двух потребителей (отзывы на локации — до 5,
// карточки бэклога — до 3).
//
// Два режима, переключается сам по наличию onUpload:
// - сущность уже существует (отзыв сохранён, карточка создана) — файл уходит
//   на сервер сразу по выбору;
// - сущности ещё нет (пишем новый отзыв или новую карточку) — файлы копятся
//   в pending и показываются превью из blob, а на сервер уезжают в момент
//   сохранения. Прикладывать фото можно сразу, а не «сначала сохрани».
//
// Компонент НЕ ходит в API сам — вызывающий отдаёт onUpload/onDelete.

import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";

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
  // Чтение (без кнопок выбора/удаления) — для чужих карточек и отзывов.
  readOnly?: boolean;
  // Есть — файл уходит на сервер сразу; нет — копится в pending.
  onUpload?: (file: File) => Promise<AttachedPhoto>;
  onDelete?: (photoId: string) => Promise<void>;
  onChange?: (photos: AttachedPhoto[]) => void;
  // Отложенные файлы (режим «сущности ещё нет») — хранит родитель, чтобы
  // отправить их после создания отзыва/карточки.
  pending?: File[];
  onPendingChange?: (files: File[]) => void;
  label?: string;
  hint?: string;
};

// HEIC/HEIF — снимки с айфона: без них в списке галерея на iOS предлагала бы
// только старые фото или молча конвертировала. Сервер перекодирует в JPEG.
const ACCEPT = "image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif";
const IMAGE_EXTENSION = /\.(jpe?g|png|webp|heic|heif)$/i;

export function PhotoAttachments({
  photos,
  maxPhotos,
  readOnly = false,
  onUpload,
  onDelete,
  onChange,
  pending,
  onPendingChange,
  label = "Фото",
  hint,
}: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const pendingFiles = useMemo(() => pending ?? [], [pending]);
  // blob-ссылки живут ровно столько, сколько файл в списке: иначе вкладка
  // копила бы их до перезагрузки страницы.
  const pendingUrls = useMemo(
    () => pendingFiles.map((file) => URL.createObjectURL(file)),
    [pendingFiles],
  );
  useEffect(() => {
    return () => {
      pendingUrls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [pendingUrls]);

  const total = photos.length + pendingFiles.length;
  const canAdd = !readOnly && total < maxPhotos;

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setError(null);
    const chosen = Array.from(files).slice(0, maxPhotos - total);

    if (onUpload == null) {
      // Сущности ещё нет — просто запоминаем файлы до сохранения.
      onPendingChange?.([...pendingFiles, ...chosen]);
      if (inputRef.current) inputRef.current.value = "";
      return;
    }

    setBusy(true);
    // Грузим по одному и последовательно: лимит считает сервер, и при
    // параллельной отправке последние запросы всё равно упёрлись бы в отказ.
    const added: AttachedPhoto[] = [];
    try {
      for (const file of chosen) {
        added.push(await onUpload(file));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить фото");
    } finally {
      if (added.length > 0) onChange?.([...photos, ...added]);
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function handleDeleteUploaded(photo: AttachedPhoto) {
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

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragOver(false);
    if (!canAdd) return;
    // Из проводника прилетают любые файлы — берём только картинки, остальное
    // молча игнорируем (сервер их всё равно не примет).
    // У HEIC из проводника MIME-тип бывает пустым — смотрим ещё на расширение.
    const images = Array.from(event.dataTransfer.files).filter(
      (file) => file.type.startsWith("image/") || IMAGE_EXTENSION.test(file.name),
    );
    if (images.length === 0) return;
    const list = new DataTransfer();
    images.forEach((file) => list.items.add(file));
    void handleFiles(list.files);
  }

  function handleDeletePending(index: number) {
    onPendingChange?.(pendingFiles.filter((_file, i) => i !== index));
  }

  if (readOnly && photos.length === 0) return null;

  return (
    <div
      className={`photo-attachments${dragOver ? " photo-attachments--dragover" : ""}`}
      onDragOver={(event: DragEvent<HTMLDivElement>) => {
        if (!canAdd) return;
        // Без preventDefault браузер просто откроет файл в новой вкладке.
        event.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={(event: DragEvent<HTMLDivElement>) => {
        // Уход на дочерний элемент — не выход из зоны.
        if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
        setDragOver(false);
      }}
      onDrop={handleDrop}
    >
      {!readOnly && (
        <div className="photo-attachments__head">
          <span className="photo-attachments__label">{label}</span>
          <span className="photo-attachments__counter">
            {total} из {maxPhotos}
          </span>
        </div>
      )}

      <div className="photo-attachments__grid">
        {photos.map((photo) => (
          <div className="photo-attachments__item" key={photo.id}>
            <button
              type="button"
              className="photo-attachments__thumb"
              onClick={() => setLightbox(photo.url)}
              aria-label="Открыть фото"
            >
              <img src={photo.url} alt="" loading="lazy" />
            </button>
            {!readOnly && onDelete != null && (
              <button
                type="button"
                className="photo-attachments__remove"
                onClick={() => void handleDeleteUploaded(photo)}
                disabled={busy}
                aria-label="Удалить фото"
              >
                ×
              </button>
            )}
          </div>
        ))}

        {pendingFiles.map((file, index) => (
          <div className="photo-attachments__item" key={`${file.name}-${index}`}>
            <button
              type="button"
              className="photo-attachments__thumb"
              onClick={() => setLightbox(pendingUrls[index])}
              aria-label="Открыть фото"
            >
              <img src={pendingUrls[index]} alt="" />
            </button>
            {!readOnly && (
              <button
                type="button"
                className="photo-attachments__remove"
                onClick={() => handleDeletePending(index)}
                aria-label="Убрать фото"
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
            <span className="photo-attachments__add-icon">{busy ? "…" : "＋"}</span>
            <span className="photo-attachments__add-text">
              {busy ? "Загрузка" : "Добавить"}
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

      {!readOnly && canAdd && (
        <p className="photo-attachments__hint">
          {dragOver ? "Отпустите — добавим фото" : "Можно перетащить файлы сюда"}
          {hint != null && !dragOver ? ` · ${hint}` : ""}
        </p>
      )}
      {error != null && <p className="photo-attachments__error">{error}</p>}

      {lightbox != null && (
        <ImageLightbox src={lightbox} onClose={() => setLightbox(null)} />
      )}
    </div>
  );
}
