import { useEffect, useState, type RefObject } from "react";

type PinnedMeBarProps = {
  /** Ref на настоящую строку «меня» в таблице (может быть не отрендерена). */
  rowRef: RefObject<HTMLTableRowElement | null>;
  rank: number;
  name: string;
  value: string | number;
  onShow: () => void;
  /** Ключ пересборки наблюдателя: строка перерендерилась (сортировка, догрузка). */
  watchKey?: unknown;
};

/**
 * Липкая «моя строка» у нижнего края: видна, пока настоящая строка рейтинга
 * вне экрана. Кнопка докручивает (и при необходимости догружает) до неё.
 */
export function PinnedMeBar({ rowRef, rank, name, value, onShow, watchKey }: PinnedMeBarProps) {
  const [rowVisible, setRowVisible] = useState(false);

  useEffect(() => {
    const row = rowRef.current;
    if (!row) {
      // Строка не отрендерена (за пределами load-more) — значит, точно не видна.
      setRowVisible(false);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => setRowVisible(entries[0]?.isIntersecting ?? false),
      { rootMargin: "0px 0px -56px 0px" },
    );
    observer.observe(row);
    return () => observer.disconnect();
  }, [rowRef, watchKey]);

  return (
    <div
      className={`pinned-me-bar${rowVisible ? " pinned-me-hidden" : ""}`}
      aria-hidden={rowVisible}
    >
      <span className="pinned-me-rank">{rank}</span>
      <span className="pinned-me-name">
        {name} <span className="muted">(вы)</span>
      </span>
      <span className="pinned-me-value">{value}</span>
      <button type="button" className="btn btn-sm" onClick={onShow}>
        Показать
      </button>
    </div>
  );
}
