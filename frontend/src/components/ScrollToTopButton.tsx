import { useEffect, useState } from "react";

const SCROLL_TOP_THRESHOLD = 600;

/**
 * Сколько пикселей надо пролистать в одну сторону, чтобы кнопка появилась или
 * спряталась: дрожь пальца на месте не должна её мигать.
 */
const DIRECTION_SLACK = 24;

/**
 * Кнопка «наверх» для длинных списков.
 *
 * Появляется, когда страница уже прокручена ниже порога И человек листает
 * вверх; пока он читает таблицу вниз — спрятана. Раньше кнопка висела всегда:
 * на телефоне она стояла ровно над колонкой значения — числом «Всего», ради
 * которого открывают рейтинг, временем в протоколе, — а на компьютере при
 * 901–1511px над последним столбцом (проверка 26.09.2026). Движение вверх —
 * и есть желание вернуться к началу, тут кнопка и нужна.
 */
export function ScrollToTopButton({ threshold = SCROLL_TOP_THRESHOLD }: { threshold?: number }) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    let lastY = window.scrollY;
    // Откуда началось текущее движение: при смене направления точка
    // переносится, и порог DIRECTION_SLACK считается заново.
    let anchorY = lastY;
    let direction: "up" | "down" | null = null;

    const handleScroll = () => {
      const y = window.scrollY;
      if (y <= threshold) {
        setVisible(false);
        lastY = y;
        anchorY = y;
        direction = null;
        return;
      }
      const next = y < lastY ? "up" : y > lastY ? "down" : direction;
      if (next !== direction) {
        direction = next;
        anchorY = lastY;
      }
      lastY = y;
      if (Math.abs(y - anchorY) >= DIRECTION_SLACK) {
        setVisible(direction === "up");
      }
    };
    handleScroll();
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, [threshold]);

  if (!visible) {
    return null;
  }

  return (
    <button
      type="button"
      className="scroll-top-btn"
      aria-label="Наверх страницы"
      title="Наверх страницы"
      onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
    >
      ↑
    </button>
  );
}
