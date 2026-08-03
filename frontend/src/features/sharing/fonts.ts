// Вшитый шрифт постеров.
//
// Системный стек (как в старом рендерере) давал разный вид карточки на
// macOS/Windows/Android и не совпадал с серверным рендером OG. Поэтому постеры
// набираются вшитым шрифтом; woff2-файлы лежат в репо (лицензия SIL OFL),
// html-to-image заинлайнит их в экспортируемый PNG.
//
// Golos Text — дефолт; Inter подключён для сравнения глазами (переключатель
// ?font=inter на странице /share, решение по шрифту за Дмитрием).

import golos400Cyr from "./fonts/golos-400-cyrillic.woff2";
import golos400Lat from "./fonts/golos-400-latin.woff2";
import golos600Cyr from "./fonts/golos-600-cyrillic.woff2";
import golos600Lat from "./fonts/golos-600-latin.woff2";
import golos800Cyr from "./fonts/golos-800-cyrillic.woff2";
import golos800Lat from "./fonts/golos-800-latin.woff2";
import inter400Cyr from "./fonts/inter-400-cyrillic.woff2";
import inter400Lat from "./fonts/inter-400-latin.woff2";
import inter600Cyr from "./fonts/inter-600-cyrillic.woff2";
import inter600Lat from "./fonts/inter-600-latin.woff2";
import inter800Cyr from "./fonts/inter-800-cyrillic.woff2";
import inter800Lat from "./fonts/inter-800-latin.woff2";

const CYRILLIC_RANGE = "U+0301, U+0400-045F, U+0490-0491, U+04B0-04B1, U+2116";
const LATIN_RANGE = "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+2000-206F, U+2074, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD";

type FontSource = { family: string; weight: number; url: string; range: string };

const FONT_SOURCES: FontSource[] = [
  { family: "Golos Text", weight: 400, url: golos400Cyr, range: CYRILLIC_RANGE },
  { family: "Golos Text", weight: 400, url: golos400Lat, range: LATIN_RANGE },
  { family: "Golos Text", weight: 600, url: golos600Cyr, range: CYRILLIC_RANGE },
  { family: "Golos Text", weight: 600, url: golos600Lat, range: LATIN_RANGE },
  { family: "Golos Text", weight: 800, url: golos800Cyr, range: CYRILLIC_RANGE },
  { family: "Golos Text", weight: 800, url: golos800Lat, range: LATIN_RANGE },
  { family: "Inter", weight: 400, url: inter400Cyr, range: CYRILLIC_RANGE },
  { family: "Inter", weight: 400, url: inter400Lat, range: LATIN_RANGE },
  { family: "Inter", weight: 600, url: inter600Cyr, range: CYRILLIC_RANGE },
  { family: "Inter", weight: 600, url: inter600Lat, range: LATIN_RANGE },
  { family: "Inter", weight: 800, url: inter800Cyr, range: CYRILLIC_RANGE },
  { family: "Inter", weight: 800, url: inter800Lat, range: LATIN_RANGE },
];

export type ShareFontId = "golos" | "inter";

export function shareFontFamily(font: ShareFontId): string {
  const family = font === "inter" ? "Inter" : "Golos Text";
  return `"${family}", system-ui, -apple-system, sans-serif`;
}

/** Шрифт для сравнения: ?font=inter (по умолчанию Golos Text). */
export function shareFontFromQuery(): ShareFontId {
  try {
    return new URLSearchParams(window.location.search).get("font") === "inter" ? "inter" : "golos";
  } catch {
    return "golos";
  }
}

let injected = false;

/** Однократно добавляет @font-face постеров в документ. */
export function injectShareFontFaces(): void {
  if (injected || typeof document === "undefined") {
    return;
  }
  injected = true;
  const css = FONT_SOURCES.map(
    (font) => `@font-face {
  font-family: '${font.family}';
  font-style: normal;
  font-weight: ${font.weight};
  font-display: block;
  src: url(${font.url}) format('woff2');
  unicode-range: ${font.range};
}`,
  ).join("\n");
  const style = document.createElement("style");
  style.dataset.shareFonts = "true";
  style.textContent = css;
  document.head.appendChild(style);
}

/**
 * Дожидается загрузки начертаний перед рендером/экспортом: иначе первый
 * постер уедет с фолбэком, а html-to-image снимет «не тот» шрифт.
 */
export async function ensureShareFontsLoaded(font: ShareFontId): Promise<void> {
  injectShareFontFaces();
  if (typeof document === "undefined" || !("fonts" in document)) {
    return;
  }
  const family = font === "inter" ? "Inter" : "Golos Text";
  try {
    await Promise.all(
      [400, 600, 800].map((weight) => document.fonts.load(`${weight} 16px "${family}"`, "Тест 0123456789")),
    );
  } catch {
    // Не блокируем шторку из-за шрифта: карточка отрисуется фолбэком.
  }
}
