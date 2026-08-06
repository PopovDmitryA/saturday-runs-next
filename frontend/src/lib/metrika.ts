/**
 * Яндекс.Метрика, SPA-часть.
 *
 * Сам счётчик (111350728) подключён статическим сниппетом в index.html — так
 * проверка Метрики «код установлен» находит его в сыром HTML, а вебвизор
 * получает настоящую страницу. Но сниппет считает только ПЕРВУЮ загрузку:
 * дальше роутер меняет адрес без перезагрузки, и переходы Метрика сама не
 * видит. Их дошлифовывает reportMetrikaHit — вызов на каждой смене пути в
 * App.usePageMeta, там же, где меняется заголовок вкладки.
 *
 * Первый вызов пропускается: initial-загрузку уже посчитал init (опция
 * url: location.href в сниппете), второй hit задвоил бы просмотр.
 *
 * Зачем счётчик вообще (решение Дмитрия 06.08.2026, рекомендация Вебмастера):
 * позиции он не поднимает — Яндекс прямо говорит, что Метрика не фактор
 * ранжирования. Реальная польза — «Обход по счётчикам» в связке с
 * Вебмастером (робот быстрее узнаёт о живых страницах) и поведенческая
 * аналитика, которой наша page_analytics не занимается.
 */
export const METRIKA_COUNTER_ID = 111350728;

type YmFn = (id: number, action: string, ...args: unknown[]) => void;

declare global {
  interface Window {
    ym?: YmFn;
  }
}

let initialHitDone = false;

/** Просмотр страницы при SPA-переходе; первую загрузку уже посчитал init. */
export function reportMetrikaHit(path: string): void {
  if (!initialHitDone) {
    initialHitDone = true;
    return;
  }
  if (typeof window === "undefined" || !window.ym) {
    return;
  }
  window.ym(METRIKA_COUNTER_ID, "hit", path, { title: document.title });
}
