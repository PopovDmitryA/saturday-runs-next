import { useCallback, useEffect, useRef } from "react";

/**
 * Тени-подсказки горизонтального скролла: пока справа (или слева) остаётся
 * скрытый контент, у соответствующего края хоста висит градиентная тень.
 * Скроллбары на телефоне не видны, тень — единственный намёк, что содержимое
 * листается вбок.
 *
 * Хост должен нести класс .tshadow-host (стили в index.css), скролл-контейнер —
 * иметь overflow-x: auto. Отдаём оба ref-а: hostRef на обёртку, scrollRef на
 * прокручиваемый элемент.
 */
export function useScrollShadows<
  H extends HTMLElement = HTMLDivElement,
  S extends HTMLElement = HTMLDivElement,
>() {
  const hostRef = useRef<H | null>(null);
  const scrollRef = useRef<S | null>(null);

  const update = useCallback(() => {
    const host = hostRef.current;
    const sc = scrollRef.current;
    if (!host || !sc) {
      return;
    }
    const maxLeft = sc.scrollWidth - sc.clientWidth;
    host.classList.toggle("tshadow-left", sc.scrollLeft > 4);
    host.classList.toggle("tshadow-right", maxLeft > 4 && sc.scrollLeft < maxLeft - 4);
  }, []);

  useEffect(() => {
    const sc = scrollRef.current;
    if (!sc) {
      return;
    }
    update();
    sc.addEventListener("scroll", update, { passive: true });
    const observer = new ResizeObserver(update);
    observer.observe(sc);
    // Ширина контента меняется и без ресайза контейнера (переключение
    // «Кратко | Полно», догрузка строк) — следим и за самим содержимым.
    const content = sc.firstElementChild;
    if (content) {
      observer.observe(content);
    }
    return () => {
      sc.removeEventListener("scroll", update);
      observer.disconnect();
    };
  }, [update]);

  return { hostRef, scrollRef, update };
}
