from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_optional_user
from app.config import get_settings
from app.core.admin import is_admin_user
from app.core.image_processing import MAX_UPLOAD_BYTES
from app.db.session import get_db
from app.models import BacklogCard, BacklogCardPhoto, BacklogCardStatus, BacklogCardType, User
from app.schemas.backlog import (
    BacklogCardCreateRequest,
    BacklogCardListResponse,
    BacklogCardResponse,
    BacklogCardUpdateRequest,
    BacklogCommentCreateRequest,
    BacklogCommentListResponse,
    BacklogCommentResponse,
    BacklogVoteRequest,
)
from app.schemas.photo import PhotoResponse
from app.services.backlog_service import (
    BacklogError,
    create_card,
    create_comment,
    get_card,
    list_cards,
    list_comments,
    update_card,
    vote_card,
)
from app.services.photo_service import (
    PhotoError,
    add_backlog_photo,
    delete_backlog_photo,
    photo_payload,
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


@router.patch("/cards/{card_id}", response_model=BacklogCardResponse)
def backlog_update_card(
    card_id: UUID,
    body: BacklogCardUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> BacklogCardResponse:
    # Права проверяет сервис: автор правит свою карточку, админ — любую.
    try:
        return update_card(
            db,
            card_id,
            editor=user,
            type_=body.type,
            category=body.category,
            title=body.title,
            description=body.description,
            is_anonymous=body.is_anonymous,
            status=body.status,
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


@router.post(
    "/cards/{card_id}/photos",
    response_model=PhotoResponse,
    status_code=status.HTTP_201_CREATED,
)
async def backlog_add_photo(
    card_id: UUID,
    file: UploadFile,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> PhotoResponse:
    """Приложить фото к карточке (до 3). Автор — к своей, админ — к любой."""
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Файл больше 15 МБ — выберите изображение поменьше",
        )
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой файл")

    card = db.query(BacklogCard).filter(BacklogCard.id == card_id).one_or_none()
    if card is None:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    if card.author_user_id != user.id and not is_admin_user(user, get_settings()):
        raise HTTPException(status_code=403, detail="Можно прикладывать фото только к своей карточке")

    try:
        photo = add_backlog_photo(db, card.id, raw)
    except PhotoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    db.commit()
    payload = photo_payload(photo)
    return PhotoResponse(id=payload.id, url=payload.url, width=payload.width, height=payload.height)


@router.delete("/photos/{photo_id}", status_code=204)
def backlog_remove_photo(
    photo_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    photo = db.query(BacklogCardPhoto).filter(BacklogCardPhoto.id == photo_id).one_or_none()
    if photo is None:
        raise HTTPException(status_code=404, detail="Фото не найдено")
    card = db.query(BacklogCard).filter(BacklogCard.id == photo.card_id).one()
    if card.author_user_id != user.id and not is_admin_user(user, get_settings()):
        raise HTTPException(status_code=403, detail="Удалять можно только свои фото")
    delete_backlog_photo(db, photo)
    db.commit()


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
