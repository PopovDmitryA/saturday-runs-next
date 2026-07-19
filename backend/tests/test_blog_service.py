"""Юнит-тесты блога: витрина, сортировки, темы, клики, валидация."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.services.blog_service import (
    HOME_FRESH_COUNT,
    BlogPostError,
    create_post,
    delete_post,
    list_all_posts,
    list_published_posts,
    list_topics,
    pick_home_posts,
    register_click,
    update_post,
)


def _make_post(db: Session, *, title: str, days_ago: int, topic: str | None = None, published: bool = True):
    return create_post(
        db,
        title=title,
        teaser=f"Затравка «{title}»",
        telegram_url="https://t.me/popov_way/1",
        topic=topic,
        published_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        is_published=published,
    )


def test_recent_sort_and_unpublished_hidden(db_session: Session) -> None:
    _make_post(db_session, title="старый", days_ago=10)
    _make_post(db_session, title="свежий", days_ago=1)
    _make_post(db_session, title="скрытый", days_ago=0, published=False)

    posts = list_published_posts(db_session)
    titles = [post.title for post in posts]
    assert "скрытый" not in titles
    assert titles.index("свежий") < titles.index("старый")
    # Неопубликованный виден только в админ-списке.
    assert "скрытый" in [post.title for post in list_all_posts(db_session)]


def test_popular_sort_by_clicks(db_session: Session) -> None:
    quiet = _make_post(db_session, title="тихий", days_ago=1)
    hit = _make_post(db_session, title="хит", days_ago=5)
    for _ in range(3):
        register_click(db_session, hit.id)

    posts = list_published_posts(db_session, sort="popular")
    titles = [post.title for post in posts]
    assert titles.index("хит") < titles.index("тихий")
    assert register_click(db_session, quiet.id) == 1


def test_click_on_unpublished_or_missing_is_404(db_session: Session) -> None:
    hidden = _make_post(db_session, title="скрытый", days_ago=0, published=False)
    with pytest.raises(BlogPostError):
        register_click(db_session, hidden.id)
    with pytest.raises(BlogPostError):
        register_click(db_session, uuid4())


def test_topics_and_topic_filter(db_session: Session) -> None:
    _make_post(db_session, title="а", days_ago=1, topic="Люди")
    _make_post(db_session, title="б", days_ago=2, topic="Люди")
    _make_post(db_session, title="в", days_ago=3, topic="Рекорды")
    _make_post(db_session, title="без темы", days_ago=4)
    _make_post(db_session, title="скрытая тема", days_ago=0, topic="Люди", published=False)

    topics = {row["topic"]: row["posts"] for row in list_topics(db_session)}
    assert topics.get("Люди") == 2
    assert topics.get("Рекорды") == 1

    filtered = list_published_posts(db_session, topic="Люди")
    assert {post.title for post in filtered} == {"а", "б"}


def test_home_posts_keep_fresh_first(db_session: Session) -> None:
    for day in range(12):
        _make_post(db_session, title=f"пост-{day}", days_ago=day)

    picked = pick_home_posts(db_session, limit=6)
    assert len(picked) == 6
    fresh_titles = [post.title for post in picked[:HOME_FRESH_COUNT]]
    assert fresh_titles == ["пост-0", "пост-1", "пост-2"]
    assert len({post.id for post in picked}) == 6


def test_validation_and_update(db_session: Session) -> None:
    with pytest.raises(BlogPostError):
        create_post(
            db_session,
            title="  ",
            teaser="текст",
            telegram_url="https://t.me/popov_way/1",
            topic=None,
            published_at=None,
            is_published=True,
        )
    with pytest.raises(BlogPostError):
        create_post(
            db_session,
            title="пост",
            teaser="текст",
            telegram_url="https://example.com/post",
            topic=None,
            published_at=None,
            is_published=True,
        )

    post = _make_post(db_session, title="до правки", days_ago=1, topic="Люди")
    updated = update_post(
        db_session,
        post.id,
        title="после правки",
        teaser="новая затравка",
        telegram_url="https://t.me/popov_way/2",
        topic="  ",
        published_at=None,
        is_published=False,
    )
    assert updated.title == "после правки"
    assert updated.topic is None
    assert updated.is_published is False

    delete_post(db_session, post.id)
    with pytest.raises(BlogPostError):
        update_post(
            db_session,
            post.id,
            title="х",
            teaser="х",
            telegram_url="https://t.me/popov_way/3",
            topic=None,
            published_at=None,
            is_published=True,
        )
