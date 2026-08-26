// Подбор кегля под отведённую коробку.
//
// Раньше размер героя выбирался на глаз по длине строки («длиннее четырёх
// символов — мельче»), и текстовые герои вылезали за свою колонку: роль
// волонтёра «Сканирование штрих-кодов» в широком формате наезжала на плитки,
// «Раздача карточек позиций» — на бренд-футер. Плюс лесенка «--long» вообще не
// доезжала до широкого и квадратного форматов: их правило .s2-card--wide
// .s2-hero-value специфичнее одиночного класса .s2-hero-value--long.
//
// Теперь слова меряются canvas-ом тем же шрифтом и начертанием, что в
// карточке, раскладываются жадным переносом, и берётся самый крупный кегль из
// лесенки, при котором текст умещается по ширине, высоте и числу строк.
//
// Почему canvas, а не измерение узла: карточка живёт в трёх контекстах —
// превью шторки (ужато transform-ом), скрытая экспортная сцена и серверный
// рендер OG. Измерение по строке не зависит ни от одного из них и не требует
// второго прохода раскладки, поэтому превью, PNG и OG-картинка совпадают.

/** Коробка под строку: сколько места есть и какими кеглями его занимать. */
export type FitBox = {
  maxWidthPx: number;
  maxHeightPx: number;
  maxLines: number;
  /** Кегли в em от крупного к мелкому; первый — размер «как задумано». */
  sizesEm: number[];
};

/**
 * Интерлиньяж многострочного текста. Однострочный герой (число, время) живёт
 * с line-height: 1 — так его блок не «толстеет» и вертикальное центрирование
 * остаётся прежним. У нескольких строк такой плотности мало: у «й» бревис
 * залезает на строку выше.
 */
export const MULTILINE_LINE_HEIGHT = 1.08;

export type FitResult = {
  sizeEm: number;
  lines: number;
  /** Готовое значение line-height для найденной раскладки. */
  lineHeight: number;
};

export type FitFont = {
  /** Кегль корня карточки: 1em в нативном размере формата. */
  basePx: number;
  fontFamily: string;
  fontWeight: number;
};

// undefined — ещё не пробовали, null — canvas недоступен (SSR, старый движок).
let cachedContext: CanvasRenderingContext2D | null | undefined;

function measureContext(): CanvasRenderingContext2D | null {
  if (cachedContext === undefined) {
    try {
      cachedContext = document.createElement("canvas").getContext("2d");
    } catch {
      cachedContext = null;
    }
  }
  return cachedContext;
}

/** Ширина среднего символа в долях кегля — запасной путь без canvas. */
const FALLBACK_CHAR_WIDTH = 0.62;

function textWidth(text: string, fontPx: number, font: FitFont): number {
  const context = measureContext();
  if (!context) {
    return text.length * fontPx * FALLBACK_CHAR_WIDTH;
  }
  context.font = `${font.fontWeight} ${fontPx}px ${font.fontFamily}`;
  return context.measureText(text).width;
}

/**
 * Сколько строк займёт текст при жадном переносе по пробелам.
 * null — хотя бы одно слово не влезает в строку целиком (кегль велик).
 *
 * Переносы внутри слова (по дефису, break-word) не моделируем: браузер их
 * умеет, но оценка «слово не влезло» в запас — карточка получит кегль мельче,
 * а не текст поверх плиток.
 */
function wrappedLines(text: string, fontPx: number, maxWidthPx: number, font: FitFont): number | null {
  // Только обычные пробелы: неразрывный (formatInt ставит его в разрядах) —
  // не точка переноса.
  const words = text.split(/[ \t\n]+/).filter(Boolean);
  if (words.length === 0) {
    return 1;
  }
  let lines = 1;
  let current = "";
  for (const word of words) {
    if (textWidth(word, fontPx, font) > maxWidthPx) {
      return null;
    }
    const candidate = current ? `${current} ${word}` : word;
    if (!current || textWidth(candidate, fontPx, font) <= maxWidthPx) {
      current = candidate;
    } else {
      lines += 1;
      current = word;
    }
  }
  return lines;
}

function lineHeightFor(lines: number): number {
  return lines > 1 ? MULTILINE_LINE_HEIGHT : 1;
}

/**
 * Самая крупная раскладка из лесенки, которая умещается в коробку.
 * Если не влезает даже мелкий кегль — отдаём его: остаток дорежет
 * overflow-wrap: anywhere в CSS.
 */
export function fitText(text: string, box: FitBox, font: FitFont): FitResult {
  for (const sizeEm of box.sizesEm) {
    const fontPx = sizeEm * font.basePx;
    const lines = wrappedLines(text, fontPx, box.maxWidthPx, font);
    if (lines === null || lines > box.maxLines) {
      continue;
    }
    const lineHeight = lineHeightFor(lines);
    if (lines * fontPx * lineHeight > box.maxHeightPx) {
      continue;
    }
    return { sizeEm, lines, lineHeight };
  }
  const smallest = box.sizesEm[box.sizesEm.length - 1];
  const lines = wrappedLines(text, smallest * font.basePx, box.maxWidthPx, font) ?? box.maxLines;
  return { sizeEm: smallest, lines, lineHeight: lineHeightFor(lines) };
}
