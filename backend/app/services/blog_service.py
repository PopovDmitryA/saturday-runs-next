"""Блог: затравки постов Telegram-канала, витрина на главной и админ-CRUD."""

from __future__ import annotations

import random
import re
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import BlogPost

# Сколько карточек отдаёт витрина главной: свежие + случайные из остальных.
HOME_POSTS_LIMIT = 9
# Сколько самых свежих постов гарантированно попадают в витрину.
HOME_FRESH_COUNT = 3

_TELEGRAM_POST_URL_RE = re.compile(r"^https://t\.me/[\w@/-]+$")


class BlogPostError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _validate_payload(
    *, title: str, teaser: str, telegram_url: str, topic: str | None
) -> tuple[str, str, str, str | None]:
    title = title.strip()
    teaser = teaser.strip()
    url = telegram_url.strip()
    clean_topic = (topic or "").strip() or None
    if not title:
        raise BlogPostError("Заголовок не может быть пустым")
    if not teaser:
        raise BlogPostError("Затравка не может быть пустой")
    if not _TELEGRAM_POST_URL_RE.match(url):
        raise BlogPostError("Ссылка должна быть вида https://t.me/канал/номер_поста")
    return title, teaser, url, clean_topic


def list_published_posts(db: Session, *, topic: str | None = None) -> list[BlogPost]:
    query = select(BlogPost).where(BlogPost.is_published.is_(True))
    if topic:
        query = query.where(BlogPost.topic == topic)
    query = query.order_by(BlogPost.published_at.desc())
    return list(db.execute(query).scalars().all())


def list_topics(db: Session) -> list[dict[str, int | str]]:
    rows = db.execute(
        select(BlogPost.topic, func.count())
        .where(BlogPost.is_published.is_(True), BlogPost.topic.is_not(None))
        .group_by(BlogPost.topic)
        .order_by(func.count().desc(), BlogPost.topic)
    ).all()
    return [{"topic": topic, "posts": count} for topic, count in rows]


def pick_home_posts(db: Session, *, limit: int = HOME_POSTS_LIMIT) -> list[BlogPost]:
    """Карточки для карусели на главной.

    Самые свежие идут первыми и попадают в выдачу всегда, остаток набирается
    случайно из прочих опубликованных — витрина меняется между визитами,
    но свежий пост не тонет.
    """
    posts = list_published_posts(db)
    fresh = posts[:HOME_FRESH_COUNT]
    rest = posts[HOME_FRESH_COUNT:]
    random.shuffle(rest)
    return fresh + rest[: max(0, limit - len(fresh))]


def register_click(db: Session, post_id: UUID) -> int:
    """Атомарный инкремент счётчика переходов; 404 для неопубликованных."""
    row = db.execute(
        update(BlogPost)
        .where(BlogPost.id == post_id, BlogPost.is_published.is_(True))
        .values(clicks_count=BlogPost.clicks_count + 1)
        .returning(BlogPost.clicks_count)
    ).scalar_one_or_none()
    if row is None:
        raise BlogPostError("Пост не найден", status_code=404)
    db.commit()
    return int(row)


def list_all_posts(db: Session) -> list[BlogPost]:
    return list(
        db.execute(select(BlogPost).order_by(BlogPost.published_at.desc())).scalars().all()
    )


def create_post(
    db: Session,
    *,
    title: str,
    teaser: str,
    telegram_url: str,
    topic: str | None,
    published_at: datetime | None,
    is_published: bool,
) -> BlogPost:
    title, teaser, url, clean_topic = _validate_payload(
        title=title, teaser=teaser, telegram_url=telegram_url, topic=topic
    )
    post = BlogPost(
        title=title,
        teaser=teaser,
        telegram_url=url,
        topic=clean_topic,
        is_published=is_published,
    )
    if published_at is not None:
        post.published_at = published_at
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


def _get_post(db: Session, post_id: UUID) -> BlogPost:
    post = db.get(BlogPost, post_id)
    if post is None:
        raise BlogPostError("Пост не найден", status_code=404)
    return post


def update_post(
    db: Session,
    post_id: UUID,
    *,
    title: str,
    teaser: str,
    telegram_url: str,
    topic: str | None,
    published_at: datetime | None,
    is_published: bool,
) -> BlogPost:
    post = _get_post(db, post_id)
    title, teaser, url, clean_topic = _validate_payload(
        title=title, teaser=teaser, telegram_url=telegram_url, topic=topic
    )
    post.title = title
    post.teaser = teaser
    post.telegram_url = url
    post.topic = clean_topic
    post.is_published = is_published
    if published_at is not None:
        post.published_at = published_at
    db.commit()
    db.refresh(post)
    return post


def delete_post(db: Session, post_id: UUID) -> None:
    post = _get_post(db, post_id)
    db.delete(post)
    db.commit()
