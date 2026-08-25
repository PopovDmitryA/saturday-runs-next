/**
 * Пагинация публичных разделов: «Назад — номера страниц — Вперёд».
 *
 * Номера — настоящие ссылки (`hrefForPage`), чтобы страницу можно было
 * скопировать, открыть в новой вкладке и отдать поисковику. Клик перехватываем
 * сами: `onNavigate` меняет адрес через history.pushState, поэтому перезагрузки
 * не происходит. Без preventDefault сработал бы общий обработчик ссылок сайта
 * (useAppPath), а он на смену только строки запроса страницу не перерисовывает.
 */
import type { MouseEvent, ReactNode } from "react";

export type PaginationProps = {
  page: number;
  pages: number;
  /** Адрес страницы N — он же href ссылки. */
  hrefForPage: (page: number) => string;
  /** Переход без перезагрузки. Не задан — ссылки работают обычной навигацией. */
  onNavigate?: (page: number) => void;
  /** Подпись слева, например «Показано 1–10 из 32». */
  summary?: ReactNode;
  /** Заголовок для скринридера. */
  label?: string;
};

/**
 * Номера страниц с многоточиями: первая, последняя, текущая и по соседу с
 * каждой стороны. На узком экране длинный ряд номеров всё равно не помещается,
 * поэтому середина сворачивается в «…» уже начиная с семи страниц.
 */
export function paginationRange(page: number, pages: number): (number | "gap")[] {
  if (pages <= 7) {
    return Array.from({ length: pages }, (_, index) => index + 1);
  }
  const around = new Set<number>([1, pages, page, page - 1, page + 1]);
  // У краёв показываем на соседа больше, иначе ряд «прыгает» по ширине.
  if (page <= 3) {
    around.add(2).add(3).add(4);
  }
  if (page >= pages - 2) {
    around.add(pages - 1).add(pages - 2).add(pages - 3);
  }
  const numbers = [...around].filter((value) => value >= 1 && value <= pages).sort((a, b) => a - b);
  const result: (number | "gap")[] = [];
  let previous = 0;
  for (const value of numbers) {
    if (previous && value - previous > 1) {
      result.push("gap");
    }
    result.push(value);
    previous = value;
  }
  return result;
}

export function Pagination({
  page,
  pages,
  hrefForPage,
  onNavigate,
  summary,
  label = "Страницы",
}: PaginationProps) {
  if (pages <= 1) {
    return null;
  }

  const go = (target: number) => (event: MouseEvent<HTMLAnchorElement>) => {
    if (!onNavigate || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    event.preventDefault();
    onNavigate(target);
  };

  const arrow = (target: number, text: string, disabled: boolean, extra: string) =>
    disabled ? (
      <span className={`pager-item pager-arrow ${extra} pager-item-disabled`} aria-hidden="true">
        {text}
      </span>
    ) : (
      <a className={`pager-item pager-arrow ${extra}`} href={hrefForPage(target)} onClick={go(target)}>
        {text}
      </a>
    );

  return (
    <nav className="pager" aria-label={label}>
      {summary && <p className="pager-summary">{summary}</p>}
      <div className="pager-controls">
        {arrow(page - 1, "← Назад", page <= 1, "pager-prev")}
        <div className="pager-pages">
          {paginationRange(page, pages).map((value, index) =>
            value === "gap" ? (
              <span key={`gap-${index}`} className="pager-gap" aria-hidden="true">
                …
              </span>
            ) : value === page ? (
              <span key={value} className="pager-item pager-item-current" aria-current="page">
                {value}
              </span>
            ) : (
              <a
                key={value}
                className="pager-item"
                href={hrefForPage(value)}
                onClick={go(value)}
                aria-label={`Страница ${value}`}
              >
                {value}
              </a>
            ),
          )}
        </div>
        {arrow(page + 1, "Вперёд →", page >= pages, "pager-next")}
      </div>
    </nav>
  );
}
