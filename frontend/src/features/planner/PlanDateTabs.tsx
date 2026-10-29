import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { formatInt } from "../../lib/format";
import { planDayLabel } from "./PlanModal";
import { verbForCount } from "./PlanPeople";
import { formatDayMonthShort } from "./plannerDates";
import "./planner.css";

/** Что нужно вкладке: дата, особая ли она и сколько на неё планируют. */
export type PlanTabDate = {
  date: string;
  kind: "regular" | "special";
  title: string | null;
  total: number;
};

/**
 * Подсказка и имя вкладки для экранных читалок: «Пт 1 января 2027, Новогодний
 * старт, планируют 8». На самой вкладке только «★ 1 янв (8)» — полная дата и
 * название особого старта живут здесь, строки под вкладками больше нет.
 */
function tabLabel(item: PlanTabDate): string {
  const parts = [planDayLabel(item.date, true)];
  if (item.kind === "special") {
    parts.push(item.title || "особый старт");
  }
  parts.push(`${verbForCount(item.total, "планирует", "планируют")} ${formatInt(item.total)}`);
  return parts.join(", ");
}

function prefersReducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Ряд вкладок дат в шапке блока. Панели дат рисует сам блок сразу под шапкой,
 * поэтому выбор живёт у него; panelId — адрес панели своей даты (у блока
 * локации панель у каждой даты своя, у «Кто у нас бежит» — одна на все). Тот же
 * ряд — на странице «Кто у нас бежит» в оргкабинете: даты там выглядят так же,
 * как на странице локации. Не влезает —
 * ряд прокручивается вбок внутри себя, выбранная подкручивается в видимую
 * часть, а край, за которым есть ещё вкладки, тает: обрезанная вкладка
 * сразу за заголовком иначе читается как сломанный текст.
 */
export function PlanDateTabs({
  dates,
  current,
  tabId,
  panelId,
  onSelect,
}: {
  dates: PlanTabDate[];
  current: PlanTabDate;
  tabId: (date: string) => string;
  panelId: (date: string) => string;
  onSelect: (date: string) => void;
}) {
  const tabRefs = useRef(new Map<string, HTMLButtonElement>());
  const listRef = useRef<HTMLDivElement>(null);
  // Есть ли вкладки за левым и правым краем ряда.
  const [more, setMore] = useState({ start: false, end: false });

  useEffect(() => {
    const list = listRef.current;
    if (!list) {
      return;
    }
    // Запас в 3px — отступ под рамку фокуса: у края ряда прокрутка не всегда
    // доходит до нуля ровно.
    const update = () => {
      const start = list.scrollLeft > 3;
      const end = list.scrollLeft < list.scrollWidth - list.clientWidth - 3;
      setMore((prev) => (prev.start === start && prev.end === end ? prev : { start, end }));
    };
    update();
    list.addEventListener("scroll", update, { passive: true });
    const observer = new ResizeObserver(update);
    observer.observe(list);
    return () => {
      list.removeEventListener("scroll", update);
      observer.disconnect();
    };
  }, [dates]);

  // По «Назад» выбранная дата восстанавливается (useRestorableState), а ряд
  // монтируется прокрученным к началу: на телефоне подсвеченной вкладки не
  // было видно. Подкручиваем сразу и без анимации — только сам ряд вбок, а не
  // scrollIntoView: тот двигал бы и страницу. Клик подкручивает сам, плавно
  // (select), поэтому здесь — только при монтировании и новых датах.
  const currentDate = current.date;
  useLayoutEffect(() => {
    const list = listRef.current;
    const tab = tabRefs.current.get(currentDate);
    if (!list || !tab) {
      return;
    }
    const listBox = list.getBoundingClientRect();
    const tabBox = tab.getBoundingClientRect();
    if (tabBox.left < listBox.left) {
      list.scrollLeft += tabBox.left - listBox.left;
    } else if (tabBox.right > listBox.right) {
      list.scrollLeft += tabBox.right - listBox.right;
    }
  }, [dates]);

  const select = (date: string, focus = false) => {
    onSelect(date);
    const tab = tabRefs.current.get(date);
    if (!tab) {
      return;
    }
    if (focus) {
      tab.focus({ preventScroll: true });
    }
    // block: nearest — страница по вертикали не дёргается, если ряд и так виден.
    tab.scrollIntoView({ block: "nearest", inline: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
  };

  // Стрелки, Home и End — как у вкладок по ARIA: фокус и выбор едут вместе.
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = dates.findIndex((item) => item.date === current.date);
    let next: number | null = null;
    if (event.key === "ArrowRight") {
      next = (index + 1) % dates.length;
    } else if (event.key === "ArrowLeft") {
      next = (index - 1 + dates.length) % dates.length;
    } else if (event.key === "Home") {
      next = 0;
    } else if (event.key === "End") {
      next = dates.length - 1;
    }
    if (next === null) {
      return;
    }
    event.preventDefault();
    select(dates[next].date, true);
  };

  return (
    <div
      ref={listRef}
      className={`plan-loc-tabs${more.start ? " plan-loc-tabs-more-start" : ""}${more.end ? " plan-loc-tabs-more-end" : ""}`}
      role="tablist"
      aria-label="Даты стартов"
      onKeyDown={handleKeyDown}
    >
      {dates.map((item) => {
        const active = item.date === current.date;
        const special = item.kind === "special";
        const label = tabLabel(item);
        return (
          <button
            key={item.date}
            ref={(node) => {
              if (node) {
                tabRefs.current.set(item.date, node);
              } else {
                tabRefs.current.delete(item.date);
              }
            }}
            type="button"
            role="tab"
            id={tabId(item.date)}
            aria-selected={active}
            aria-controls={panelId(item.date)}
            aria-label={label}
            title={label}
            tabIndex={active ? 0 : -1}
            className={special ? "plan-loc-tab plan-loc-tab-special" : "plan-loc-tab"}
            onClick={() => select(item.date)}
          >
            {special && (
              <span className="plan-loc-tab-star" aria-hidden="true">
                ★
              </span>
            )}
            {formatDayMonthShort(item.date)} <span className="plan-loc-tab-count">({formatInt(item.total)})</span>
          </button>
        );
      })}
    </div>
  );
}
