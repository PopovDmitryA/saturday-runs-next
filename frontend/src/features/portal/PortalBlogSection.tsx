import { useCallback, useEffect, useRef, useState } from "react";
import { PORTAL_BLOG_HREF } from "../../lib/portalRoutes";
import { PortalBlogCard } from "./PortalBlogCard";
import { fetchBlogHome, type BlogPost } from "./blogTypes";

/**
 * Карусель блога на главной: свежие посты первыми, дальше случайные из
 * остальных (порядок отдаёт бэкенд). Стрелки листают по ширине карточки,
 * дорожка скроллится и пальцем — на мобильных стрелки не обязательны.
 */
export function PortalBlogSection({
  onPostsLoaded,
}: {
  /** Есть ли что показывать — оглавлению главной, чтобы не вести в пустоту. */
  onPostsLoaded?: (hasPosts: boolean) => void;
} = {}) {
  const [posts, setPosts] = useState<BlogPost[]>([]);
  const trackRef = useRef<HTMLDivElement | null>(null);
  const [canPrev, setCanPrev] = useState(false);
  const [canNext, setCanNext] = useState(false);

  useEffect(() => {
    fetchBlogHome()
      .then(setPosts)
      .catch(() => setPosts([]));
  }, []);

  useEffect(() => {
    onPostsLoaded?.(posts.length > 0);
  }, [posts.length, onPostsLoaded]);

  const updateArrows = useCallback(() => {
    const track = trackRef.current;
    if (!track) {
      return;
    }
    setCanPrev(track.scrollLeft > 4);
    setCanNext(track.scrollLeft + track.clientWidth < track.scrollWidth - 4);
  }, []);

  useEffect(() => {
    updateArrows();
    window.addEventListener("resize", updateArrows);
    return () => window.removeEventListener("resize", updateArrows);
  }, [posts, updateArrows]);

  const scrollByCard = (direction: 1 | -1) => {
    const track = trackRef.current;
    if (!track) {
      return;
    }
    const card = track.querySelector<HTMLElement>(".portal-blog-card");
    const step = card ? card.offsetWidth + 16 : track.clientWidth;
    track.scrollBy({ left: direction * step, behavior: "smooth" });
  };

  if (posts.length === 0) {
    return null;
  }

  return (
    <section id="blog" className="portal-panel portal-blog" aria-label="Блог">
      <div className="portal-panel-head">
        <div>
          <h2>Блог</h2>
          <p className="portal-panel-sub">
            Рассказы об участниках субботних пробежек и цифрах, которые за ними стоят
          </p>
        </div>
        <a className="portal-blog-all" href={PORTAL_BLOG_HREF}>
          Все посты →
        </a>
      </div>
      <div className="portal-blog-carousel">
        <button
          type="button"
          className="portal-blog-arrow prev"
          aria-label="Предыдущие посты"
          onClick={() => scrollByCard(-1)}
          disabled={!canPrev}
        >
          ‹
        </button>
        <div className="portal-blog-track" ref={trackRef} onScroll={updateArrows}>
          {posts.map((post) => (
            <PortalBlogCard key={post.id} post={post} />
          ))}
        </div>
        <button
          type="button"
          className="portal-blog-arrow next"
          aria-label="Следующие посты"
          onClick={() => scrollByCard(1)}
          disabled={!canNext}
        >
          ›
        </button>
      </div>
    </section>
  );
}
