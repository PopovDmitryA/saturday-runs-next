import { registerBlogClick, type BlogPost } from "./blogTypes";

// Темы задаются в админке свободным текстом, поэтому цвет не привязан к
// конкретным названиям: берём его из палитры по хешу строки — одна и та же
// тема всегда одного цвета, новая получает свой без правок кода.
const TOPIC_TONES = 6;

function topicToneClass(topic: string): string {
  let hash = 0;
  for (let i = 0; i < topic.length; i += 1) {
    hash = (hash * 31 + topic.charCodeAt(i)) % 100_000;
  }
  return `tone-${hash % TOPIC_TONES}`;
}

function formatPostDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

/**
 * Карточка поста: затравка на сайте, полный текст и комментарии — в Telegram.
 * Вся карточка — ссылка на пост в канале; переход считаем в clicks_count.
 */
export function PortalBlogCard({ post }: { post: BlogPost }) {
  return (
    <a
      className="portal-blog-card"
      href={post.telegram_url}
      target="_blank"
      rel="noreferrer"
      onClick={() => registerBlogClick(post.id)}
    >
      <span className="portal-blog-card-meta">
        {post.topic && (
          <span className={`portal-blog-topic ${topicToneClass(post.topic)}`}>{post.topic}</span>
        )}
        <span className="portal-blog-date">{formatPostDate(post.published_at)}</span>
      </span>
      <b className="portal-blog-title">{post.title}</b>
      <span className="portal-blog-teaser">{post.teaser}</span>
      <span className="portal-blog-cta">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M21.5 2.7 2.6 10c-.9.35-.87 1.6.05 1.9l4.6 1.44 1.73 5.5c.27.86 1.36 1.06 1.92.36l2.05-2.53 4.5 3.3c.77.57 1.87.15 2.06-.79L22.9 4.1c.2-1-.55-1.72-1.4-1.4Z" />
        </svg>
        Читать продолжение
      </span>
    </a>
  );
}
