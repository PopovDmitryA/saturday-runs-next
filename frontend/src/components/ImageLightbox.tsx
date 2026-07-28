// Просмотр картинки в полный размер: затемнение на весь экран, закрытие по
// клику в любом месте и по Escape. Используется и фото-вложениями, и аватаркой.

import { useEffect } from "react";

import "./ImageLightbox.css";

type Props = {
  src: string;
  onClose: () => void;
  alt?: string;
};

export function ImageLightbox({ src, onClose, alt = "" }: Props) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="image-lightbox" role="dialog" aria-modal="true" onClick={onClose}>
      <img src={src} alt={alt} />
      <button type="button" className="image-lightbox__close" aria-label="Закрыть" onClick={onClose}>
        ×
      </button>
    </div>
  );
}
