/**
 * Яндекс.Метрика.
 *
 * Зачем (рекомендация Вебмастера, решение Дмитрия 06.08.2026): счётчик сам по
 * себе позиций не поднимает — Яндекс официально говорит, что наличие Метрики
 * не фактор ранжирования. Реальная польза для индексации другая: связка
 * Метрика↔Вебмастер включает «Обход по счётчикам» — робот узнаёт о страницах,
 * на которые реально заходят люди, и обходит их быстрее, чем дойдёт по
 * sitemap. Плюс поведенческая аналитика (глубина, отказы, источники), которой
 * наша собственная page_analytics не занимается.
 *
 * Сайт — SPA: обычный счётчик увидел бы только первую загрузку, поэтому
 * инициализация с defer:true, а каждый переход роутера репортится вручную
 * через hit() (вызов — в App.usePageMeta, там же, где меняется заголовок).
 *
 * null — счётчик выключен совсем: ни скрипта, ни запросов. Заполнить номером
 * из metrika.yandex.ru после создания счётчика.
 */
export const METRIKA_COUNTER_ID: number | null = null;

type YmFn = (id: number, action: string, ...args: unknown[]) => void;

declare global {
  interface Window {
    ym?: YmFn;
  }
}

/** Загружает счётчик. Вызывается один раз из main.tsx; без номера — no-op. */
export function initMetrika(): void {
  if (METRIKA_COUNTER_ID == null || typeof window === "undefined") {
    return;
  }
  if (window.ym) {
    return; // повторная инициализация в StrictMode/HMR не нужна
  }
  const stub: YmFn & { a?: unknown[]; l?: number } = (...args: unknown[]) => {
    (stub.a = stub.a ?? []).push(args);
  };
  stub.l = Date.now();
  window.ym = stub as unknown as YmFn;
  const script = document.createElement("script");
  script.async = true;
  script.src = "https://mc.yandex.ru/metrika/tag.js";
  document.head.appendChild(script);
  window.ym(METRIKA_COUNTER_ID, "init", {
    defer: true, // SPA: первый hit шлём сами, вместе с остальными переходами
    clickmap: true,
    trackLinks: true,
    accurateTrackBounce: true,
  });
}

/** Просмотр страницы — на каждый переход роутера (и на первую загрузку). */
export function reportMetrikaHit(path: string): void {
  if (METRIKA_COUNTER_ID == null || typeof window === "undefined" || !window.ym) {
    return;
  }
  window.ym(METRIKA_COUNTER_ID, "hit", path, { title: document.title });
}
