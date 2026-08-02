import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { PortalFooter } from "./PortalFooter";
import { PortalHeader } from "./PortalHeader";
import { fetchReleases, type SiteRelease, type SiteReleaseList } from "./releaseTypes";
import { PORTAL_HOME_HREF } from "../../lib/portalRoutes";
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

/** Публичная страница «Обновления»: история релизов сайта, новые сверху. */
export function PortalUpdatesPage() {
  const [data, setData] = useState<SiteReleaseList | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchReleases()
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
          setError(null);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setError(err.message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
          (data.items.length === 0 ? (
            <p className="portal-empty-note">Записей пока нет — скоро появятся.</p>
          ) : (
            <div className="portal-updates-list">
              {data.items.map((release: SiteRelease) => (
                <article key={release.id} className="portal-release" id={`v${release.version}`}>
                  <header className="portal-release-head">
                    <span className="portal-release-version">v{release.version}</span>
                    <h2 className="portal-release-title">{release.title}</h2>
                    <time className="portal-release-date" dateTime={release.released_at}>
                      {formatDate(release.released_at)}
                    </time>
                  </header>
                  <div className="portal-release-body">{renderReleaseBody(release.body)}</div>
                </article>
              ))}
            </div>
          ))}
      </main>
      <PortalFooter />
    </>
  );
}
