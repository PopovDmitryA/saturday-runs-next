from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.image_processing import MAX_UPLOAD_BYTES
from app.db.session import get_db
from app.models import LocationRating, LocationRatingPhoto, User
from app.schemas.rating import (
    MyRatingsResponse,
    PhotoResponse,
    RatingEligibilityResponse,
    RatingResponse,
    RatingUpsertRequest,
)
from app.services.photo_service import (
    PhotoError,
    add_rating_photo,
    delete_rating_photo,
    photo_payload,
)
from app.services.rating_service import (
    RatingError,
    delete_rating,
    list_eligible_runs,
    list_my_ratings,
    load_editable_rating,
    upsert_rating,
)

router = APIRouter(prefix="/ratings", tags=["ratings"])


@router.get("/eligible-runs", response_model=RatingEligibilityResponse)
def get_eligible_runs(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> RatingEligibilityResponse:
    return RatingEligibilityResponse.model_validate(list_eligible_runs(db, user.id))


@router.get("/mine", response_model=MyRatingsResponse)
def get_my_ratings(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> MyRatingsResponse:
    return MyRatingsResponse.model_validate(list_my_ratings(db, user.id))


@router.put("/entry/{entry_id}", response_model=RatingResponse)
def put_rating(
    entry_id: str,
    payload: RatingUpsertRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> RatingResponse:
    try:
        result = upsert_rating(
            db,
            user,
            entry_id,
            score_overall=payload.score_overall,
            score_organization=payload.score_organization,
            score_route=payload.score_route,
            score_community=payload.score_community,
            comment=payload.comment,
            is_public=payload.is_public,
        )
    except RatingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return RatingResponse.model_validate(result)


@router.post(
    "/entry/{entry_id}/photos",
    response_model=PhotoResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_photo(
    entry_id: str,
    file: UploadFile,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> PhotoResponse:
    """Приложить фото к своему отзыву (до 5 штук, пока отзыв редактируем)."""
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Файл больше 15 МБ — выберите изображение поменьше",
        )
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой файл")

    try:
        rating = load_editable_rating(db, user.id, entry_id)
        photo = add_rating_photo(db, rating, raw)
    except RatingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PhotoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    db.commit()
    payload = photo_payload(photo)
    return PhotoResponse(id=payload.id, url=payload.url, width=payload.width, height=payload.height)


@router.delete("/photos/{photo_id}", status_code=204)
def remove_photo(
    photo_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    # Join на саму оценку — чтобы чужое фото нельзя было снести по угаданному id.
    photo = (
        db.query(LocationRatingPhoto)
        .join(LocationRating, LocationRatingPhoto.rating_id == LocationRating.id)
        .filter(LocationRatingPhoto.id == photo_id, LocationRating.user_id == user.id)
        .one_or_none()
    )
    if photo is None:
        raise HTTPException(status_code=404, detail="Фото не найдено")
    delete_rating_photo(db, photo)
    db.commit()


@router.delete("/entry/{entry_id}", status_code=204)
def remove_rating(
    entry_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    try:
        deleted = delete_rating(db, user.id, entry_id)
    except RatingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Оценка не найдена")
    db.commit()
