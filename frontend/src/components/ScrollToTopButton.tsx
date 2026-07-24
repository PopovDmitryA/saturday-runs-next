import { useEffect, useState } from "react";

const SCROLL_TOP_THRESHOLD = 600;

/** Кнопка «наверх» для длинных списков — появляется, когда страница уже прокручена. */
export function ScrollToTopButton({ threshold = SCROLL_TOP_THRESHOLD }: { threshold?: number }) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const handleScroll = () => {
      setVisible(window.scrollY > threshold);
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
