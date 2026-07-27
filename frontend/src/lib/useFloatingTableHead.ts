import { useEffect, useState } from "react";

/**
 * Плавающая шапка широкой таблицы — для экранов, где таблицу листают вбок.
 *
 * Настоящий thead там прилипнуть к окну не может: широкой таблице нужен
 * overflow-x, а он по спеке делает обёртку скролл-контейнером — sticky начинает
 * отсчитываться от неё, а не от окна, и шапка уезжает вверх вместе с боксом.
 * Ограничить высоту обёртки тоже не помогает: шапка прилипает к верху бокса, но
 * сам бокс при листании страницы уходит под шапку сайта.
 *
 * Поэтому, пока таблица пересекает низ шапки сайта, показываем её копию —
 * фиксированную полосу с теми же ширинами колонок и той же прокруткой вбок.
 * На широких экранах обёртка скролл-контейнером не становится, настоящий sticky
 * работает сам, и копия не создаётся.
 *
 * Возвращает ref-колбэк для обёртки таблицы. Именно колбэк, а не ref-объект:
 * таблица появляется только после загрузки данных, и эффект по обычному ref
 * отработал бы один раз на пустой ссылке.
 */
export function useFloatingTableHead(): (node: HTMLDivElement | null) => void {
  const [wrap, setWrap] = useState<HTMLDivElement | null>(null);

  useEffect(() => {
    const table = wrap?.querySelector("table");
    const head = table?.querySelector("thead");
    if (!wrap || !table || !head) return;

    const float = document.createElement("div");
    float.className = "float-thead";
    // Для скринридера копия — шум: настоящая шапка таблицы никуда не делась.
    float.setAttribute("aria-hidden", "true");
    float.hidden = true;
    const inner = document.createElement("div");
    float.append(inner);
    // Вешаем не в body, а внутрь секции страницы: вид шапки задан правилами с
    // предком (.portal-section .data-table thead th), и в body копия вышла бы
    // другим шрифтом и без капса. Ни на одном из этих контейнеров нет
    // transform/filter — иначе fixed считался бы от них, а не от окна.
    (wrap.closest(".portal-section, .portal-cab-main") ?? document.body).append(float);

    // Клик по копии сортирует так же, как по оригиналу: обработчик висит на th,
    // поэтому достаточно повторить клик по ячейке с тем же номером.
    float.addEventListener("click", (event) => {
      const cell = (event.target as HTMLElement).closest("th");
      if (!cell?.parentElement) return;
      const index = [...cell.parentElement.children].indexOf(cell);
      const origin = head.querySelectorAll("th")[index];
      if (origin instanceof HTMLElement) origin.click();
    });

    let frame = 0;
    let needsRebuild = true;

    const rebuild = () => {
      // Копию собираем заново, а не правим: сортировка меняет и разметку шапки
      // (стрелка, подсветка колонки), и ширины колонок под новыми значениями.
      const clone = table.cloneNode(false) as HTMLTableElement;
      clone.append(head.cloneNode(true));
      clone.style.tableLayout = "fixed";
      clone.style.width = `${table.getBoundingClientRect().width}px`;
      const widths = [...head.querySelectorAll("th")].map(
        (cell) => cell.getBoundingClientRect().width,
      );
      clone.querySelectorAll("th").forEach((cell, index) => {
        cell.style.width = `${widths[index]}px`;
      });
      inner.replaceChildren(clone);
      needsRebuild = false;
    };

    const sync = () => {
      frame = 0;
      // Копия нужна, только пока обёртка сама скроллится: иначе sticky живой.
      if (getComputedStyle(wrap).overflowY === "visible") {
        float.hidden = true;
        return;
      }

      const siteHeader = document.querySelector("header");
      // Низ шапки сайта, а не её высота: шапка липкая, но на других страницах
      // её может не быть вовсе — тогда полоса встаёт к верху окна.
      const offset = Math.max(0, siteHeader?.getBoundingClientRect().bottom ?? 0);
      const box = wrap.getBoundingClientRect();
      const height = head.getBoundingClientRect().height;
      if (box.top >= offset || box.bottom <= offset + height) {
        float.hidden = true;
        return;
      }

      if (needsRebuild) rebuild();
      float.hidden = false;
      float.style.top = `${offset}px`;
      float.style.left = `${box.left}px`;
      float.style.width = `${box.width}px`;
      float.style.height = `${height}px`;
      inner.style.transform = `translateX(${-wrap.scrollLeft}px)`;
    };

    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(sync);
    };
    const invalidate = () => {
      needsRebuild = true;
      schedule();
    };

    schedule();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", invalidate);
    wrap.addEventListener("scroll", schedule, { passive: true });
    const resizeObserver = new ResizeObserver(invalidate);
    resizeObserver.observe(table);
    // Сортировка перерисовывает шапку, не меняя её размеров.
    const headObserver = new MutationObserver(invalidate);
    headObserver.observe(head, { subtree: true, childList: true, characterData: true });

    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", invalidate);
      wrap.removeEventListener("scroll", schedule);
      resizeObserver.disconnect();
      headObserver.disconnect();
      float.remove();
    };
  }, [wrap]);

  return setWrap;
}
