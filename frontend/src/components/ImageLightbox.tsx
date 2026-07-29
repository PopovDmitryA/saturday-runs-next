// Просмотр картинки в полный размер: затемнение на весь экран, кнопка закрытия,
// закрытие по клику мимо картинки и по Escape. Используется и фото-вложениями,
// и аватарками.
//
// Рендерится порталом в document.body: если оставить его на месте вызова, он
// попадает внутрь чужого stacking-контекста (сайдбар, карточка с transform), и
// тогда никакой z-index не спасает — плитки дашборда лезут поверх затемнения и
// поверх самой картинки.

import { useEffect } from "react";
import { createPortal } from "react-dom";

import "./ImageLightbox.css";

type Props = {
  src: string;
  onClose: () => void;
  alt?: string;
};

export function ImageLightbox({ src, onClose, alt = "" }: Props) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      // Лайтбокс часто открыт поверх модалки (оценка старта, карточка
      // бэклога), а та тоже закрывается по Escape. Слушаем в capture-фазе на
      // window и глушим событие: до обработчика модалки оно не дойдёт, и
      // первый Escape закроет только картинку.
      event.stopPropagation();
      event.preventDefault();
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    // Пока картинка открыта, страница под ней не должна прокручиваться.
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = previousOverflow;
    };
  }, [onClose]);

  return createPortal(
    <div className="image-lightbox" role="dialog" aria-modal="true" onClick={onClose}>
      <button type="button" className="image-lightbox__close" aria-label="Закрыть" onClick={onClose}>
        ×
      </button>
      {/* Клик по самой картинке не закрывает — закрывает клик по фону и крестик. */}
      <img src={src} alt={alt} onClick={(event) => event.stopPropagation()} />
    </div>,
    document.body,
  );
}
