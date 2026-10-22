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
  return withGaps(around, pages);
}

/**
 * Короткий ряд для телефона: первая, текущая и последняя страницы, «…» — там,
 * где между ними есть пропуск. Соседей текущей в полный ряд не прятать
 * CSS-ом: у края («1 2 3 4 … 124» на третьей) вместе с ними пропадала бы и
 * дыра — «1 3 … 124» читалось бы как «второй страницы нет» (ревью 21.10.2026).
 */
export function compactPaginationRange(page: number, pages: number): (number | "gap")[] {
  return withGaps(new Set([1, page, pages]), pages);
}

function withGaps(values: Set<number>, pages: number): (number | "gap")[] {
  const numbers = [...values].filter((value) => value >= 1 && value <= pages).sort((a, b) => a - b);
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

  // Подпись стрелки на узком экране прячется (остаётся «←»/«→»): «← Назад» и
  // «Вперёд →» переносились на две строки и отнимали место у номеров.
  // Зазор между стрелкой и подписью — gap у .pager-arrow, а не пробел в
  // тексте: внутри inline-flex подпись — отдельный элемент, и пробел у её
  // края схлопывался в «←Назад» (ревью 21.10.2026).
  const arrow = (target: number, text: ReactNode, label: string, disabled: boolean, extra: string) =>
    disabled ? (
      <span className={`pager-item pager-arrow ${extra} pager-item-disabled`} aria-hidden="true">
        {text}
      </span>
    ) : (
      <a
        className={`pager-item pager-arrow ${extra}`}
        href={hrefForPage(target)}
        onClick={go(target)}
        aria-label={label}
      >
        {text}
      </a>
    );
  const range = paginationRange(page, pages);
  // Длинный ряд (есть «…») на телефоне не помещается даже без подписей стрелок —
  // там показываем короткий: «← 1 … 37 … 124 →», на соседнюю ведут стрелки.
  // Какой из двух виден, решает CSS (.pager-pages-full / -compact).
  const compact = range.includes("gap") ? compactPaginationRange(page, pages) : null;
  const numbers = (values: (number | "gap")[]) =>
    values.map((value, index) =>
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
    );

  return (
    <nav className="pager" aria-label={label}>
      {summary && <p className="pager-summary">{summary}</p>}
      <div className="pager-controls">
        {arrow(
          page - 1,
          <>
            ←<span className="pager-arrow-label">Назад</span>
          </>,
          "Предыдущая страница",
          page <= 1,
          "pager-prev",
        )}
        <div className={compact ? "pager-pages pager-pages-full" : "pager-pages"}>{numbers(range)}</div>
        {compact && <div className="pager-pages pager-pages-compact">{numbers(compact)}</div>}
        {arrow(
          page + 1,
          <>
            <span className="pager-arrow-label">Вперёд</span>→
          </>,
          "Следующая страница",
          page >= pages,
          "pager-next",
        )}
      </div>
    </nav>
  );
}
