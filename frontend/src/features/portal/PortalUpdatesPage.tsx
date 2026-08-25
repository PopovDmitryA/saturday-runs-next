import { useCallback, useEffect, useRef, useState } from "react";
import type { MouseEvent, ReactNode } from "react";
import { Pagination } from "../../components/Pagination";
import { PortalFooter } from "./PortalFooter";
import { PortalHeader } from "./PortalHeader";
import { fetchReleases, type SiteRelease, type SiteReleaseList } from "./releaseTypes";
import { PORTAL_HOME_HREF, PORTAL_UPDATES_HREF } from "../../lib/portalRoutes";
import "./portal.css";

function formatDate(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/**
 * Текст релиза без markdown-библиотеки: блоки разделяются пустой строкой,
 * блок из строк «- …» становится списком, остальное — абзацами.
 */
function renderReleaseBody(body: string): ReactNode[] {
  return body
    .split(/\n\s*\n/)
    .map((block) => block.trim())
    .filter(Boolean)
    .map((block, index) => {
      const lines = block.split("\n").map((line) => line.trim());
      if (lines.every((line) => line.startsWith("- "))) {
        return (
          <ul key={index}>
            {lines.map((line, li) => (
              <li key={li}>{line.slice(2)}</li>
            ))}
          </ul>
        );
      }
      return <p key={index}>{block}</p>;
    });
}

/** Номер страницы из адреса (/updates?page=3); мусор и нули — первая страница. */
function pageFromLocation(): number {
  const raw = Number(new URLSearchParams(window.location.search).get("page"));
  return Number.isInteger(raw) && raw > 1 ? raw : 1;
}

/** Версия из якоря (/updates#v2.5.0) — по ней сервер найдёт нужную страницу. */
function versionFromHash(): string | null {
  const hash = decodeURIComponent(window.location.hash.replace(/^#/, ""));
  return /^v\d+\.\d+\.\d+(-fix\d+)?$/.test(hash) ? hash.slice(1) : null;
}

function pageHref(page: number): string {
  return page > 1 ? `${PORTAL_UPDATES_HREF}?page=${page}` : PORTAL_UPDATES_HREF;
}

/**
 * Ссылка на один релиз — ею делятся («смотри, вот когда появились протоколы»).
 * Сознательно без номера страницы: с каждым новым релизом старые уезжают на
 * страницу ниже, и ссылка с `?page=` протухла бы. По одному якорю сервер сам
 * находит нужную страницу (параметр version в /api/releases).
 */
function releaseHref(version: string): string {
  return `${PORTAL_UPDATES_HREF}#v${version}`;
}

/**
 * Докрутить до релиза, на который указывает якорь. Браузер обработал якорь
 * ещё при загрузке, когда никаких релизов в разметке не было, — поэтому
 * прокручиваем сами и с несколькими попытками: секция появится только после
 * того, как React отрисует пришедшую страницу.
 */
function scrollToRelease(version: string, attemptsLeft = 10): void {
  const target = document.getElementById(`v${version}`);
  if (target) {
    target.scrollIntoView({ block: "start" });
    return;
  }
  if (attemptsLeft > 0) {
    window.setTimeout(() => scrollToRelease(version, attemptsLeft - 1), 50);
  }
}

/** Публичная страница «Обновления»: история релизов, новые сверху, по страницам. */
export function PortalUpdatesPage() {
  const [page, setPage] = useState(pageFromLocation);
  const [data, setData] = useState<SiteReleaseList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // Якорь вида #v2.5.0 отрабатывает ровно один раз — при заходе по ссылке.
  // Дальше человек листает страницы сам, и возвращать его к якорю нельзя.
  const anchorVersion = useRef<string | null>(versionFromHash());
  // Какая страница уже показана. Нужно, когда сервер поправил номер (якорь или
  // страница за концом истории): после setPage эффект пошёл бы по второму
  // кругу, а перезапрашивать ровно то же самое незачем.
  const loadedPage = useRef<number | null>(null);
  // Релиз, на который указывает адрес: подсвечиваем его, чтобы пришедший по
  // ссылке сразу видел нужную запись. Через CSS :target это не сделать —
  // после history.replaceState браузер перестаёт считать элемент целью.
  const [highlighted, setHighlighted] = useState<string | null>(() => versionFromHash());

  // Кнопки «назад/вперёд» браузера должны листать страницы истории релизов,
  // а не уводить с раздела: адрес — источник правды для номера страницы.
  useEffect(() => {
    const sync = () => setPage(pageFromLocation());
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);

  useEffect(() => {
    if (loadedPage.current === page) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    const version = anchorVersion.current;
    fetchReleases({ page, version })
      .then((payload) => {
        if (cancelled) {
          return;
        }
        // Гасим якорь только после того, как ответ реально применён: иначе
        // отменённый запрос (StrictMode, быстрый повтор) уносил бы его с собой,
        // и ссылка на старый релиз открывала первую страницу.
        anchorVersion.current = null;
        loadedPage.current = payload.page;
        setData(payload);
        setError(null);
        // Сервер мог поправить номер (страница за концом истории или поиск по
        // якорю) — приводим адрес в соответствие с тем, что реально показано.
        if (payload.page !== page) {
          setPage(payload.page);
          window.history.replaceState(null, "", `${pageHref(payload.page)}${window.location.hash}`);
        }
        if (version) {
          scrollToRelease(version);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setError(err.message);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [page]);

  const goToPage = useCallback((next: number) => {
    // Адрес без якоря: он относится к релизу с прошлой страницы.
    anchorVersion.current = null;
    setHighlighted(null);
    window.history.pushState(null, "", pageHref(next));
    setPage(next);
    window.scrollTo({ top: 0 });
  }, []);

  /**
   * Клик по номеру или заголовку релиза. Уходить со страницы незачем — релиз
   * уже перед глазами: ставим его якорь в адрес (адресную строку можно
   * скопировать и отправить) и подкручиваем к нему. Без preventDefault общий
   * обработчик ссылок сайта увёл бы читателя со второй страницы на первую.
   */
  const selectRelease = useCallback((event: MouseEvent<HTMLAnchorElement>, version: string) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    event.preventDefault();
    window.history.replaceState(null, "", releaseHref(version));
    setHighlighted(version);
    scrollToRelease(version);
  }, []);

  const shownFrom = data && data.total ? (data.page - 1) * data.page_size + 1 : 0;
  const shownTo = data ? shownFrom + data.items.length - 1 : 0;

  return (
    <>
      <PortalHeader />
      <main className="portal-home portal-updates-page">
        <a href={PORTAL_HOME_HREF} className="portal-blog-back">
          ← На главную
        </a>

        <section className="portal-hero">
          <p className="portal-eyebrow">Версия за версией</p>
          <h1>Обновления</h1>
          <p className="portal-hero-lead">
            Что нового на сайте: новые разделы, улучшения и исправления.
          </p>
        </section>

        {error && <p className="portal-error">{error}</p>}
        {!data && !error && <p className="portal-loading">Загрузка обновлений…</p>}

        {data &&
          (data.total === 0 ? (
            <p className="portal-empty-note">Записей пока нет — скоро появятся.</p>
          ) : (
            <div className={`portal-updates-list${loading ? " portal-updates-list-loading" : ""}`}>
              {data.items.map((release: SiteRelease) => (
                <article
                  key={release.id}
                  className={`portal-release${
                    release.version === highlighted ? " portal-release-highlighted" : ""
                  }`}
                  id={`v${release.version}`}
                >
                  <header className="portal-release-head">
                    <a
                      className="portal-release-version"
                      href={releaseHref(release.version)}
                      onClick={(event) => selectRelease(event, release.version)}
                      title="Ссылка на этот релиз — ею можно поделиться"
                    >
                      v{release.version}
                    </a>
                    <h2 className="portal-release-title">
                      <a
                        className="portal-release-title-link"
                        href={releaseHref(release.version)}
                        onClick={(event) => selectRelease(event, release.version)}
                      >
                        {release.title}
                      </a>
                    </h2>
                    <time className="portal-release-date" dateTime={release.released_at}>
                      {formatDate(release.released_at)}
                    </time>
                  </header>
                  <div className="portal-release-body">{renderReleaseBody(release.body)}</div>
                </article>
              ))}

              <Pagination
                page={data.page}
                pages={data.pages}
                hrefForPage={pageHref}
                onNavigate={goToPage}
                label="Страницы обновлений"
                summary={`Релизы ${shownFrom}–${shownTo} из ${data.total}`}
              />
            </div>
          ))}
      </main>
      <PortalFooter />
    </>
  );
}
