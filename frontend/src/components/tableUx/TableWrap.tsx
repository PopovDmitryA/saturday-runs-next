import { useCallback, type ReactNode, type Ref } from "react";
import { useScrollShadows } from "./useScrollShadows";

type TableWrapProps = {
  children: ReactNode;
  /** Дополнительные классы для внутреннего .table-wrap (скролл-контейнера). */
  className?: string;
  /** Липкая первая колонка при горизонтальном скролле. */
  stickyFirstCol?: boolean;
  /** Проброс ref на скролл-контейнер (напр. для useFloatingTableHead). */
  innerRef?: Ref<HTMLDivElement>;
  /**
   * Проброс ref на внешний блок — его ширина равна доступной под таблицу
   * (в отличие от внутреннего, который может быть шире и скроллиться).
   * Нужен useAdaptiveColumns, чтобы понять, сколько колонок влезает.
   */
  outerRef?: Ref<HTMLDivElement>;
};

/**
 * Обёртка таблицы с тенями-подсказками горизонтального скролла: пока справа
 * (или слева) есть скрытые колонки, у соответствующего края висит градиентная
 * тень. Скроллбары на мобильных не видны, тень — единственный намёк, что
 * таблицу можно листать вбок. Сама механика тени — в useScrollShadows.
 */
export function TableWrap({
  children,
  className = "",
  stickyFirstCol = false,
  innerRef,
  outerRef,
}: TableWrapProps) {
  const { hostRef, scrollRef } = useScrollShadows<HTMLDivElement, HTMLDivElement>();

  const setScrollRef = useCallback(
    (node: HTMLDivElement | null) => {
      scrollRef.current = node;
      if (typeof innerRef === "function") {
        innerRef(node);
      } else if (innerRef && typeof innerRef === "object") {
        (innerRef as { current: HTMLDivElement | null }).current = node;
      }
    },
    [innerRef, scrollRef],
  );

  const setHostRef = useCallback(
    (node: HTMLDivElement | null) => {
      hostRef.current = node;
      if (typeof outerRef === "function") {
        // Колбэк может вернуть функцию-уборщик (React 19) — пробрасываем как есть.
        return outerRef(node);
      }
      if (outerRef && typeof outerRef === "object") {
        (outerRef as { current: HTMLDivElement | null }).current = node;
      }
    },
    [hostRef, outerRef],
  );

  return (
    <div ref={setHostRef} className="tshadow-host">
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
