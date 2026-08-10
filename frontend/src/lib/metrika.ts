/**
 * Яндекс.Метрика, SPA-часть.
 *
 * Сам счётчик (111350728) подключён статическим сниппетом в index.html — так
 * проверка Метрики «код установлен» находит его в сыром HTML, а вебвизор
 * получает настоящую страницу. Но сниппет считает только ПЕРВУЮ загрузку:
 * дальше роутер меняет адрес без перезагрузки, и переходы Метрика сама не
 * видит. Их дошлифовывает этот модуль — вызовы из App.usePageMeta.
 *
 * Почему хиты двух сортов (репорт Дмитрия 06.08.2026): у страниц-сущностей
 * (локация, журнал протоколов) заголовок с именем парка появляется только
 * после загрузки данных. Немедленный хит уносил в отчёт «Заголовки страниц»
 * родовое «Локация — run5k.run» — и просмотры размазывались между родовым и
 * настоящим названием. Для таких страниц App откладывает хит
 * (deferMetrikaHit), а сама страница досылает его после данных
 * (flushMetrikaHit) — уже с настоящим заголовком. Остальные страницы шлют
 * сразу: их заголовок известен без данных.
 *
 * Первый показ за загрузку не шлётся вовсе: его уже посчитал init из сниппета
 * (опция url: location.href), второй хит задвоил бы просмотр. Это касается и
 * отложенного хита — прямое открытие страницы локации считает init.
 */
export const METRIKA_COUNTER_ID = 111350728;

type YmFn = (id: number, action: string, ...args: unknown[]) => void;

declare global {
  interface Window {
    ym?: YmFn;
  }
}

let initialHitDone = false;
let pendingPath: string | null = null;

function send(path: string): void {
  if (!initialHitDone) {
    // Эту загрузку уже посчитал init из сниппета.
    initialHitDone = true;
    return;
  }
  if (typeof window === "undefined" || !window.ym) {
    return;
  }
  window.ym(METRIKA_COUNTER_ID, "hit", path, { title: document.title });
}

/** Просмотр страницы с заранее известным заголовком — шлётся сразу. */
export function reportMetrikaHit(path: string): void {
  // Уход со страницы-сущности до загрузки её данных: отложенный хит пропал —
  // и это честно, заголовка для него так и не появилось.
  pendingPath = null;
  send(path);
}

/** Страница-сущность: хит откладывается до загрузки данных (flushMetrikaHit). */
export function deferMetrikaHit(path: string): void {
  pendingPath = path;
}

/** Досылает отложенный хит — вызывается страницей, когда заголовок готов. */
export function flushMetrikaHit(): void {
  if (pendingPath === null) {
    return;
  }
  const path = pendingPath;
  pendingPath = null;
  send(path);
}
