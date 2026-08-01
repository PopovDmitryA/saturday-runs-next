import { useCallback, useEffect, useRef, type ReactNode, type Ref } from "react";

type TableWrapProps = {
  children: ReactNode;
  /** Дополнительные классы для внутреннего .table-wrap (скролл-контейнера). */
  className?: string;
  /** Липкая первая колонка при горизонтальном скролле. */
  stickyFirstCol?: boolean;
  /** Проброс ref на скролл-контейнер (напр. для useFloatingTableHead). */
  innerRef?: Ref<HTMLDivElement>;
};

/**
 * Обёртка таблицы с тенями-подсказками горизонтального скролла: пока справа
 * (или слева) есть скрытые колонки, у соответствующего края висит градиентная
 * тень. Скроллбары на мобильных не видны, тень — единственный намёк, что
 * таблицу можно листать вбок.
 */
export function TableWrap({ children, className = "", stickyFirstCol = false, innerRef }: TableWrapProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

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
    // «Кратко | Полно», догрузка строк) — следим и за таблицей.
    const table = sc.firstElementChild;
    if (table) {
      observer.observe(table);
    }
    return () => {
      sc.removeEventListener("scroll", update);
      observer.disconnect();
    };
  }, [update]);

  const setScrollRef = useCallback(
    (node: HTMLDivElement | null) => {
      scrollRef.current = node;
      if (typeof innerRef === "function") {
        innerRef(node);
      } else if (innerRef && typeof innerRef === "object") {
        (innerRef as { current: HTMLDivElement | null }).current = node;
      }
    },
    [innerRef],
  );

  return (
    <div ref={hostRef} className="tshadow-host">
      <div
        ref={setScrollRef}
        className={`table-wrap${stickyFirstCol ? " table-sticky-first" : ""}${
          className ? ` ${className}` : ""
        }`}
      >
        {children}
      </div>
    </div>
  );
}
