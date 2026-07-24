/** Публичный блог: типы и запросы (без авторизации, как fetchPortalHome). */

import { getOrCreateVisitorId } from "../../lib/siteVisitor";

export type BlogPost = {
  id: string;
  title: string;
  teaser: string;
  telegram_url: string;
  topic: string | null;
  published_at: string;
  clicks_count: number;
};

export type BlogTopic = { topic: string; posts: number };

export type BlogPostList = {
  items: BlogPost[];
  total: number;
  topics: BlogTopic[];
};

export async function fetchBlogHome(): Promise<BlogPost[]> {
  const response = await fetch("/api/blog/home", { credentials: "same-origin" });
  if (!response.ok) {
    throw new Error(`Не удалось загрузить блог (HTTP ${response.status})`);
  }
  const payload = (await response.json()) as { items: BlogPost[] };
  return payload.items;
}

export async function fetchBlogPosts(params: { topic?: string | null }): Promise<BlogPostList> {
  const search = new URLSearchParams();
  if (params.topic) search.set("topic", params.topic);
  const suffix = search.toString();
  const response = await fetch(`/api/blog/posts${suffix ? `?${suffix}` : ""}`, {
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new Error(`Не удалось загрузить блог (HTTP ${response.status})`);
  }
  return (await response.json()) as BlogPostList;
}

/**
 * Счётчик перехода в Telegram. sendBeacon переживает уход со страницы —
 * обычный fetch браузер может оборвать при навигации по ссылке карточки.
 *
 * В body — контекст для админской «Популярности»: анонимный ключ посетителя
 * (для залогиненных сервер заменит его на u:<user_id> по сессионной куке) и
 * страница, с которой кликнули. Body опционален для сервера.
 */
export function registerBlogClick(postId: string): void {
  const url = `/api/blog/posts/${postId}/click`;
  const body = JSON.stringify({
    visitor_key: `a:${getOrCreateVisitorId()}`,
    path: window.location.pathname,
  });
  const blob = new Blob([body], { type: "application/json" });
  if (navigator.sendBeacon?.(url, blob)) {
    return;
  }
  void fetch(url, {
    method: "POST",
    keepalive: true,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body,
  }).catch(() => {
    /* потеря одного клика не критична */
  });
}
