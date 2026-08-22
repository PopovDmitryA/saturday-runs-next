import { useEffect, useState } from "react";
import { PortalBlogCard } from "./PortalBlogCard";
import { PortalFooter } from "./PortalFooter";
import { PortalHeader } from "./PortalHeader";
import { fetchBlogPosts, type BlogPostList } from "./blogTypes";
import { PORTAL_HOME_HREF } from "../../lib/portalRoutes";
import "./portal.css";

/** Публичная страница блога: все посты, фильтр по темам. */
export function PortalBlogPage() {
  const [data, setData] = useState<BlogPostList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [topic, setTopic] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchBlogPosts({ topic })
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
  }, [topic]);

  return (
    <>
      <PortalHeader />
      <main className="portal-home portal-blog-page">
        <a href={PORTAL_HOME_HREF} className="portal-blog-back">
          ← На главную
        </a>

        <section className="portal-hero">
          <p className="portal-eyebrow">Люди · цифры · истории</p>
          <h1>Блог</h1>
          <p className="portal-hero-lead">
            Рассказы об участниках субботних пробежек и цифрах, которые за ними стоят.
          </p>
        </section>

        {error && <p className="portal-error">{error}</p>}
        {!data && !error && <p className="portal-loading">Загрузка постов…</p>}

        {data && (
          <>
            {data.topics.length > 0 && (
              <div
                className="portal-period portal-blog-topics portal-blog-controls"
                role="tablist"
                aria-label="Темы"
              >
                <button
                  type="button"
                  className={topic === null ? "active" : ""}
                  onClick={() => setTopic(null)}
                >
                  Все ({data.topics.reduce((sum, row) => sum + row.posts, 0)})
                </button>
                {data.topics.map((row) => (
                  <button
                    key={row.topic}
                    type="button"
                    className={topic === row.topic ? "active" : ""}
                    onClick={() => setTopic(row.topic)}
                  >
                    {row.topic} ({row.posts})
                  </button>
                ))}
              </div>
            )}

            {data.items.length === 0 ? (
              <p className="portal-empty-note">Постов пока нет — скоро появятся.</p>
            ) : (
              <div className="portal-blog-grid">
                {data.items.map((post) => (
                  <PortalBlogCard key={post.id} post={post} />
                ))}
              </div>
            )}
          </>
        )}
      </main>
      <PortalFooter />
    </>
  );
}
