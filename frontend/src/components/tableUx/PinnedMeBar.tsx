import { useEffect, useState, type RefObject } from "react";

type PinnedMeBarProps = {
  /** Ref на настоящую строку «меня» в таблице (может быть не отрендерена). */
  rowRef: RefObject<HTMLTableRowElement | null>;
  /** Ref на саму таблицу: выше неё бар не показываем. */
  tableRef: RefObject<HTMLElement | null>;
  rank: number;
  name: string;
  value: string | number;
  onShow: () => void;
  /** Ключ пересборки наблюдателя: строка перерендерилась (сортировка, догрузка). */
  watchKey?: unknown;
};

/**
 * Липкая «моя строка» у нижнего края: появляется, когда пользователь доскроллил
 * до таблицы, и прячется, как только настоящая строка попала на экран.
 * Кнопка докручивает (и при необходимости догружает) до неё.
 */
export function PinnedMeBar({
  rowRef,
  tableRef,
  rank,
  name,
  value,
  onShow,
  watchKey,
}: PinnedMeBarProps) {
  const [rowVisible, setRowVisible] = useState(false);
  const [tableReached, setTableReached] = useState(false);

  useEffect(() => {
    const row = rowRef.current;
    if (!row) {
      // Строка не отрендерена (за пределами load-more) — значит точно не видна.
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

  // Над шапкой страницы бар выглядел висящим не пойми над чем — показываем его
  // только когда таблица рейтинга дошла до экрана.
  useEffect(() => {
    const table = tableRef.current;
    if (!table) {
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => setTableReached(entries[0]?.isIntersecting ?? false),
      { rootMargin: "0px 0px -120px 0px" },
    );
    observer.observe(table);
    return () => observer.disconnect();
  }, [tableRef]);

  const hidden = rowVisible || !tableReached;

  // Кнопка «наверх» живёт в том же углу — пока бар виден, сдвигаем её выше
  // (класс читает CSS страницы рейтингов).
  useEffect(() => {
    document.body.classList.toggle("has-pinned-me", !hidden);
    return () => document.body.classList.remove("has-pinned-me");
  }, [hidden]);

  return (
    <div className={`pinned-me-bar${hidden ? " pinned-me-hidden" : ""}`} aria-hidden={hidden}>
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
