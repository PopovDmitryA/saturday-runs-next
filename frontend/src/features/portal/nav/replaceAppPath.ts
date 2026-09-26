/**
 * Перейти на другой адрес без перезагрузки и без новой записи в истории —
 * как location.replace, но внутри нашего роутера (hooks/useAppPath).
 *
 * Нужно там, где страница сама решает, что человеку сюда не надо: «Мои
 * локации» при одной своей локации сразу открывают её кабинет. Раньше это
 * делалось location.replace — полная загрузка документа и всего кода заново
 * (code-8).
 *
 * replaceState оставляет ключ записи истории прежним (lib/historyEntry), а
 * роутер узнаёт о смене адреса по popstate — его и посылаем. Для памяти
 * навигации это «возврат на ту же запись»: снимок и прокрутка остаются свои.
 */
export function replaceAppPath(href: string): void {
  window.history.replaceState(window.history.state, "", href);
  window.dispatchEvent(new PopStateEvent("popstate", { state: window.history.state }));
}
