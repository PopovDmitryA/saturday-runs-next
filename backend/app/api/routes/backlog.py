from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_optional_user
from app.db.session import get_db
from app.models import BacklogCardStatus, BacklogCardType, User
from app.schemas.backlog import (
    BacklogCardCreateRequest,
    BacklogCardListResponse,
    BacklogCardResponse,
    BacklogCommentCreateRequest,
    BacklogCommentListResponse,
    BacklogCommentResponse,
    BacklogVoteRequest,
)
from app.services.backlog_service import (
    BacklogError,
    create_card,
    create_comment,
    get_card,
    list_cards,
    list_comments,
    vote_card,
)

# Просмотр открыт всем, включая анонимов — это витрина предложений. Писать
# (карточка/голос/комментарий) может только залогиненный пользователь сайта:
# такие роуты держат get_current_user и отдельно 401 не документируем тут.
router = APIRouter(prefix="/backlog", tags=["backlog"])


@router.get("/cards", response_model=BacklogCardListResponse)
def backlog_cards(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User | None, Depends(get_optional_user)],
    type: Annotated[BacklogCardType | None, Query()] = None,
    category: Annotated[str | None, Query(max_length=64)] = None,
    status_: Annotated[BacklogCardStatus | None, Query(alias="status")] = None,
) -> BacklogCardListResponse:
    viewer_id = user.id if user is not None else None
    items = list_cards(db, viewer_id=viewer_id, type_=type, category=category, status=status_)
    return BacklogCardListResponse(items=items, total=len(items))


@router.get("/cards/{card_id}", response_model=BacklogCardResponse)
def backlog_card_detail(
    card_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User | None, Depends(get_optional_user)],
) -> BacklogCardResponse:
    try:
        return get_card(db, card_id, viewer_id=user.id if user is not None else None)
    except BacklogError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/cards", response_model=BacklogCardResponse, status_code=status.HTTP_201_CREATED)
def backlog_create_card(
    body: BacklogCardCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> BacklogCardResponse:
    try:
        return create_card(
            db,
            author_id=user.id,
            type_=body.type,
            category=body.category,
            title=body.title,
            description=body.description,
            is_anonymous=body.is_anonymous,
        )
    except BacklogError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/cards/{card_id}/vote", response_model=BacklogCardResponse)
def backlog_vote(
    card_id: UUID,
    body: BacklogVoteRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> BacklogCardResponse:
    try:
        return vote_card(db, card_id, user_id=user.id, value=body.value)
    except BacklogError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/cards/{card_id}/comments", response_model=BacklogCommentListResponse)
def backlog_comments(
    card_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User | None, Depends(get_optional_user)],
) -> BacklogCommentListResponse:
    try:
        items = list_comments(db, card_id, viewer_id=user.id if user is not None else None)
        return BacklogCommentListResponse(items=items)
    except BacklogError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/cards/{card_id}/comments", response_model=BacklogCommentResponse, status_code=status.HTTP_201_CREATED)
def backlog_create_comment(
    card_id: UUID,
    body: BacklogCommentCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> BacklogCommentResponse:
    try:
        return create_comment(db, card_id, author_id=user.id, body=body.body, is_anonymous=body.is_anonymous)
    except BacklogError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
