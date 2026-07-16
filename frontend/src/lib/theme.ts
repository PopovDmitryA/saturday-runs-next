import { useCallback, useSyncExternalStore } from "react";

export type Theme = "light" | "dark";

// Стартовое значение data-theme выставляет inline-скрипт в index.html
// (до первой отрисовки, чтобы не мигала светлая тема).
const STORAGE_KEY = "theme";
const THEME_EVENT = "app-theme-change";

// Цвет адресной строки мобильных браузеров — совпадает с фоном шапки
// (--surface). Дублируется в inline-скрипте index.html.
const META_THEME_COLORS: Record<Theme, string> = {
  light: "#ffffff",
  dark: "#1a2332",
};

function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function storedTheme(): Theme | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "dark" || value === "light" ? value : null;
  } catch {
    return null;
  }
}

// Кроссфейд при смене темы: класс временно включает transition на всех
// элементах (см. :root.theme-transition в index.css).
const TRANSITION_CLASS = "theme-transition";
const TRANSITION_MS = 300;
let transitionTimer: number | undefined;

function enableThemeTransition(): void {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    return;
  }
  document.documentElement.classList.add(TRANSITION_CLASS);
  window.clearTimeout(transitionTimer);
  transitionTimer = window.setTimeout(() => {
    document.documentElement.classList.remove(TRANSITION_CLASS);
  }, TRANSITION_MS);
}

/** Применяет тему к DOM без записи в localStorage (для «следования за ОС»). */
function setDomTheme(theme: Theme): void {
  enableThemeTransition();
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", META_THEME_COLORS[theme]);
  window.dispatchEvent(new Event(THEME_EVENT));
}

export function applyTheme(theme: Theme): void {
  setDomTheme(theme);
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // приватный режим — тема просто не сохранится
  }
}

// Пока тема не выбрана вручную — живо следуем за системной.
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (event) => {
  if (storedTheme() === null) {
    setDomTheme(event.matches ? "dark" : "light");
  }
});

// Переключение в одной вкладке подхватывают остальные.
window.addEventListener("storage", (event) => {
  if (event.key === STORAGE_KEY && (event.newValue === "dark" || event.newValue === "light")) {
    setDomTheme(event.newValue);
  }
});

function subscribe(callback: () => void): () => void {
  window.addEventListener(THEME_EVENT, callback);
  return () => window.removeEventListener(THEME_EVENT, callback);
}

export function useTheme(): [Theme, () => void] {
  const theme = useSyncExternalStore(subscribe, currentTheme);
  const toggle = useCallback(() => {
    applyTheme(currentTheme() === "dark" ? "light" : "dark");
  }, []);
  return [theme, toggle];
}
