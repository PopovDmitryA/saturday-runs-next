import { useEffect, useState } from "react";
import { scrollForNavigation } from "../lib/scrollMemory";

export function normalizeAppPath(pathname = window.location.pathname): string {
  const decoded = decodeURIComponent(pathname);
  const collapsed = decoded.replace(/\/+/g, "/");
  return collapsed.replace(/\/$/, "") || "/";
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
      // таблицы, на которую он смотрит. Возврат назад (popstate) сюда не
      // доходит — позицию там возвращает lib/scrollMemory.
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
