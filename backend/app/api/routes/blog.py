from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.blog import (
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
    post_id: UUID, db: Annotated[Session, Depends(get_db)]
) -> BlogClickResponse:
    try:
        clicks = register_click(db, post_id)
    except BlogPostError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return BlogClickResponse(clicks_count=clicks)
