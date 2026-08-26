import { useEffect, useState } from "react";

export function normalizeAppPath(pathname = window.location.pathname): string {
  const decoded = decodeURIComponent(pathname);
  const collapsed = decoded.replace(/\/+/g, "/");
  return collapsed.replace(/\/$/, "") || "/";
}

/**
 * Новая страница должна открываться с начала.
 *
 * Роутинг у нас свой (pushState + перерисовка), а браузер при pushState скролл
 * не трогает: перейдя из середины журнала протоколов в сам протокол, читатель
 * попадал в его середину — «как будто уже проскроллил». Возврат назад
 * (popstate) не трогаем: там позицию восстанавливает сам браузер.
 *
 * Ссылка с якорем (`/dashboard#profiles`) докручивает до своей секции — и не
 * сразу, а после перерисовки: элемента с этим id на старой странице нет.
 */
function scrollForNavigation(hash: string): void {
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

export function useAppPath(): string {
  const [path, setPath] = useState(normalizeAppPath);

  useEffect(() => {
    const sync = () => setPath(normalizeAppPath());

    window.addEventListener("popstate", sync);
    window.addEventListener("pageshow", (event) => {
      if (event.persisted) {
        sync();
      }
    });

    const onClick = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0) {
        return;
      }
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
      }

      const anchor = (event.target as Element | null)?.closest("a[href]") as HTMLAnchorElement | null;
      if (!anchor || anchor.target === "_blank" || anchor.hasAttribute("download")) {
        return;
      }

      const href = anchor.getAttribute("href");
      if (!href || href.startsWith("#") || href.startsWith("mailto:") || href.startsWith("tel:")) {
        return;
      }

      const url = new URL(href, window.location.href);
      if (url.origin !== window.location.origin) {
        return;
      }

      // API and other non-SPA paths must use a full navigation (OAuth, downloads, etc.).
      if (url.pathname.startsWith("/api/") || anchor.hasAttribute("data-full-nav")) {
        return;
      }

      const nextPath = normalizeAppPath(url.pathname);
      const pathChanged = nextPath !== normalizeAppPath();
      if (!pathChanged && url.search === window.location.search) {
        return;
      }

      event.preventDefault();
      window.history.pushState(null, "", `${url.pathname}${url.search}${url.hash}`);
      sync();
      // Смена только строки запроса (фильтр, страница таблицы) — это та же
      // страница: сбрасывать скролл там значило бы отбрасывать читателя от
      // таблицы, на которую он смотрит.
      if (pathChanged) {
        scrollForNavigation(url.hash);
      }
    };

    document.addEventListener("click", onClick);
    return () => {
      window.removeEventListener("popstate", sync);
      document.removeEventListener("click", onClick);
    };
  }, []);

  return path;
}
