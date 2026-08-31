from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_optional_user
from app.config import Settings, get_settings
from app.core.admin import is_admin_user
from app.core.bot_detection import is_bot_user_agent
from app.db.session import get_db
from app.models import User
from app.schemas.blog import (
    BlogClickRequest,
    BlogClickResponse,
    BlogHomeResponse,
    BlogPostListResponse,
    BlogPostResponse,
    BlogTopicResponse,
)
from app.services.blog_service import (
    BlogPostError,
    list_published_posts,
    list_topics,
    pick_home_posts,
    register_click,
)
from app.services.page_analytics_service import record_blog_post_click

# Публичный раздел: блог — витрина канала для анонимов, авторизация не нужна.
router = APIRouter(prefix="/blog", tags=["blog"])


@router.get("/home", response_model=BlogHomeResponse)
def blog_home(db: Annotated[Session, Depends(get_db)]) -> BlogHomeResponse:
    posts = pick_home_posts(db)
    return BlogHomeResponse(items=[BlogPostResponse.model_validate(post) for post in posts])


@router.get("/posts", response_model=BlogPostListResponse)
def blog_posts(
    db: Annotated[Session, Depends(get_db)],
    topic: Annotated[str | None, Query(max_length=64)] = None,
) -> BlogPostListResponse:
    posts = list_published_posts(db, topic=topic)
    return BlogPostListResponse(
        items=[BlogPostResponse.model_validate(post) for post in posts],
        total=len(posts),
        topics=[BlogTopicResponse.model_validate(row) for row in list_topics(db)],
    )


@router.post("/posts/{post_id}/click", response_model=BlogClickResponse)
def blog_post_click(
    post_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    viewer: Annotated[User | None, Depends(get_optional_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    body: BlogClickRequest | None = None,
) -> BlogClickResponse:
    try:
        clicks = register_click(db, post_id)
    except BlogPostError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    # В «Популярность» клик не пишем для админа — по той же логике, что и
    # просмотры страниц в /api/stats/pageview: обходы сайта для проверки не
    # должны двигать аналитику. Публичный clicks_count при этом растёт как рос.
    is_bot = is_bot_user_agent(request.headers.get("user-agent"))
    if not is_bot and (viewer is None or not is_admin_user(viewer, settings)):
        record_blog_post_click(
            db,
            path=body.path if body else None,
            visitor_key=body.visitor_key if body else None,
            viewer_user_id=viewer.id if viewer is not None else None,
        )
    return BlogClickResponse(clicks_count=clicks)
