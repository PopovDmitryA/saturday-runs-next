/**
 * Позиция прокрутки на запись истории.
 *
 * Раньше этим занимался браузер (history.scrollRestoration = "auto"), но в SPA
 * он промахивается: на popstate высота документа ещё нулевая — старую страницу
 * уже размонтировали, новую ещё не нарисовали и не загрузили, — восстанавливать
 * некуда, и читатель оказывался наверху. Поэтому берём восстановление на себя:
 * позицию помним по ключу записи (см. historyEntry) и докручиваем, когда
 * содержимое доросло до нужной высоты.
 */

import { readEntryValue, writeEntryValue } from "./entryMemory";
import { currentEntryKey, onEntryChange } from "./historyEntry";

const NAME = "scroll";
/** Дольше ждать нет смысла: страница либо отрисовалась, либо сломалась. */
const CHASE_TIMEOUT_MS = 2500;

let stopChase: (() => void) | null = null;

/**
 * Новая страница открывается с начала.
 *
 * Браузер при pushState скролл не трогает: перейдя из середины журнала
 * протоколов в сам протокол, читатель попадал в его середину — «как будто уже
 * проскроллил».
 *
 * Ссылка с якорем (`/dashboard#profiles`) докручивает до своей секции — и не
 * сразу, а после перерисовки: элемента с этим id на старой странице нет.
 */
export function scrollForNavigation(hash: string): void {
  stopChase?.();
  const anchorId = hash.startsWith("#") ? hash.slice(1) : hash;
  if (!anchorId) {
    window.scrollTo(0, 0);
    return;
  }
  // Секция появляется не в этом кадре: сначала React перерисует страницу, а
  // содержимое может приехать ещё и запросом. Пробуем несколько кадров, потом
  // сдаёмся и показываем начало страницы.
  let attempts = 10;
  const tryScroll = () => {
    const target = document.getElementById(decodeURIComponent(anchorId));
    if (target) {
      target.scrollIntoView({ block: "start" });
      return;
    }
    attempts -= 1;
    if (attempts > 0) {
      requestAnimationFrame(tryScroll);
    } else {
      window.scrollTo(0, 0);
    }
  };
  window.scrollTo(0, 0);
  requestAnimationFrame(tryScroll);
}

/**
 * Догоняем сохранённую позицию: страница дорисовывается и догружает данные,
 * поэтому кадр за кадром прокручиваем настолько, насколько она уже выросла.
 * Как только человек тронул колесо или экран сам — отступаем: он теперь главный.
 */
function chase(target: number): void {
  stopChase?.();
  const deadline = performance.now() + CHASE_TIMEOUT_MS;
  let frame = 0;
  let settled = 0;

  const finish = () => {
    cancelAnimationFrame(frame);
    window.removeEventListener("wheel", finish);
    window.removeEventListener("touchstart", finish);
    window.removeEventListener("pointerdown", finish);
    window.removeEventListener("keydown", finish);
    stopChase = null;
  };
  stopChase = finish;
  window.addEventListener("wheel", finish, { passive: true });
  window.addEventListener("touchstart", finish, { passive: true });
  window.addEventListener("pointerdown", finish, { passive: true });
  window.addEventListener("keydown", finish);

  const tick = () => {
    const limit = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
    const next = Math.min(target, limit);
    if (Math.abs(window.scrollY - next) > 1) {
      window.scrollTo(0, next);
    }
    // Цель взята и страница перестала расти — дело сделано. Пары кадров хватает,
    // чтобы отличить «дорисовалось» от «пришёл ещё кусок данных».
    settled = limit >= target ? settled + 1 : 0;
    if (settled >= 3 || performance.now() > deadline) {
      finish();
      return;
    }
    frame = requestAnimationFrame(tick);
  };
  frame = requestAnimationFrame(tick);
}

export function restoreEntryScroll(key: string): void {
  const target = readEntryValue<number>(NAME, key);
  if (typeof target !== "number") {
    // Запись без снимка: так бывает при движении вперёд по истории на страницу,
    // которую в этой жизни вкладки ещё не видели. Ведём себя как при обычном
    // переходе — якорь или начало страницы.
    scrollForNavigation(window.location.hash);
    return;
  }
  if (target <= 0) {
    window.scrollTo(0, 0);
    return;
  }
  chase(target);
}

export function installScrollMemory(): void {
  if (typeof window === "undefined") {
    return;
  }
  if ("scrollRestoration" in window.history) {
    window.history.scrollRestoration = "manual";
  }
  onEntryChange(({ from, to, reason }) => {
    // Момент перехода — единственный, когда позиция уходящей страницы ещё на
    // экране: React перерисует всё уже после.
    writeEntryValue(NAME, window.scrollY, from);
    if (reason === "pop") {
      restoreEntryScroll(to);
    } else {
      stopChase?.();
    }
  });
  window.addEventListener("pagehide", () => {
    writeEntryValue(NAME, window.scrollY);
  });
  // Документ перезагрузился (F5 или возврат с внешнего сайта мимо bfcache):
  // scrollRestoration выключен, значит вернуть позицию — тоже наша забота.
  restoreEntryScroll(currentEntryKey());
}
