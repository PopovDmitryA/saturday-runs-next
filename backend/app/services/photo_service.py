"""Фото пользователей: отзывы на локации (до 5) и карточки бэклога (до 3).

Общий слой над двумя таблицами: одинаковые правила (обработка картинки,
лимит на сущность, запись в хранилище, удаление файла вслед за строкой) и
разные права доступа, которые задаёт вызывающий роут.

Порядок операций везде один: сначала кладём файл в хранилище, потом строку в
БД. При падении БД остаётся файл-сирота в бакете (мусор, но безвредный), при
обратном порядке в БД была бы строка с битой ссылкой — это видно пользователю.
При удалении наоборот: сначала строка, потом файл.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.image_processing import ImageProcessingError, process_photo
from app.core.media_storage import MediaStorageError, build_key, get_media_storage
from app.models import BacklogCardPhoto, Location, LocationRating, LocationRatingPhoto

MAX_RATING_PHOTOS = 5
MAX_BACKLOG_PHOTOS = 3


class PhotoError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class PhotoPayload:
    id: UUID
    url: str
    width: int
    height: int


def photo_payload(photo: LocationRatingPhoto | BacklogCardPhoto) -> PhotoPayload:
    return PhotoPayload(
        id=photo.id,
        url=get_media_storage().public_url(photo.storage_key),
        width=photo.width,
        height=photo.height,
    )


def _rating_photo_count(db: Session, rating_id: UUID) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(LocationRatingPhoto)
            .where(LocationRatingPhoto.rating_id == rating_id)
        ).scalar_one()
    )


def _backlog_photo_count(db: Session, card_id: UUID) -> int:
    return int(
        db.execute(
            select(func.count()).select_from(BacklogCardPhoto).where(BacklogCardPhoto.card_id == card_id)
        ).scalar_one()
    )


def _next_rating_sort_order(db: Session, rating_id: UUID) -> int:
    current = db.execute(
        select(func.coalesce(func.max(LocationRatingPhoto.sort_order), -1)).where(
            LocationRatingPhoto.rating_id == rating_id
        )
    ).scalar_one()
    return int(current) + 1


def _next_backlog_sort_order(db: Session, card_id: UUID) -> int:
    current = db.execute(
        select(func.coalesce(func.max(BacklogCardPhoto.sort_order), -1)).where(
            BacklogCardPhoto.card_id == card_id
        )
    ).scalar_one()
    return int(current) + 1


def _store(
    raw: bytes, prefix: str, *, segments: Sequence[str] = ()
) -> tuple[str, bytes, int, int]:
    try:
        processed, width, height = process_photo(raw)
    except ImageProcessingError as exc:
        raise PhotoError(str(exc)) from exc
    key = build_key(prefix, segments=segments)
    try:
        get_media_storage().put(key, processed)
    except MediaStorageError as exc:
        raise PhotoError("Хранилище картинок недоступно, попробуйте позже", status_code=503) from exc
    return key, processed, width, height


def _drop_file(storage_key: str) -> None:
    # Файл удаляем «мягко»: строка в БД уже снята, и падение хранилища не
    # должно превращаться в 500 для пользователя, который просто убрал фото.
    try:
        get_media_storage().delete(storage_key)
    except MediaStorageError:
        pass


def add_rating_photo(db: Session, rating: LocationRating, raw: bytes) -> LocationRatingPhoto:
    """Фото к отзыву. Ключ — `location-reviews/{slug локации}/{id отзыва}/…`:
    по пути в бакете сразу видно, к чему относится картинка.

    Id участника в путь сознательно НЕ кладём (решение Дмитрия 29.07.2026):
    ключ — это публичный URL, и общий сегмент участника позволял бы связать
    между собой все его отзывы, включая анонимные.
    """
    rating_id = rating.id
    if _rating_photo_count(db, rating_id) >= MAX_RATING_PHOTOS:
        raise PhotoError(f"К отзыву можно приложить не больше {MAX_RATING_PHOTOS} фото")
    location_slug = db.execute(
        select(Location.external_key).where(Location.id == rating.location_id)
    ).scalar_one_or_none()
    key, processed, width, height = _store(
        raw,
        get_settings().s3_prefix_location_reviews,
        segments=(location_slug or "location", str(rating_id)),
    )
    photo = LocationRatingPhoto(
        rating_id=rating_id,
        storage_key=key,
        width=width,
        height=height,
        byte_size=len(processed),
        sort_order=_next_rating_sort_order(db, rating_id),
    )
    db.add(photo)
    db.flush()
    return photo


def add_backlog_photo(db: Session, card_id: UUID, raw: bytes) -> BacklogCardPhoto:
    if _backlog_photo_count(db, card_id) >= MAX_BACKLOG_PHOTOS:
        raise PhotoError(f"К карточке можно приложить не больше {MAX_BACKLOG_PHOTOS} фото")
    key, processed, width, height = _store(raw, get_settings().s3_prefix_backlog)
    photo = BacklogCardPhoto(
        card_id=card_id,
        storage_key=key,
        width=width,
        height=height,
        byte_size=len(processed),
        sort_order=_next_backlog_sort_order(db, card_id),
    )
    db.add(photo)
    db.flush()
    return photo


def list_rating_photos(db: Session, rating_ids: list[UUID]) -> dict[UUID, list[PhotoPayload]]:
    """Фото сразу для пачки отзывов — списки отзывов рисуются одним запросом."""
    if not rating_ids:
        return {}
    rows = db.execute(
        select(LocationRatingPhoto)
        .where(LocationRatingPhoto.rating_id.in_(rating_ids))
        .order_by(LocationRatingPhoto.rating_id, LocationRatingPhoto.sort_order)
    ).scalars()
    grouped: dict[UUID, list[PhotoPayload]] = {}
    for photo in rows:
        grouped.setdefault(photo.rating_id, []).append(photo_payload(photo))
    return grouped


def list_backlog_photos(db: Session, card_ids: list[UUID]) -> dict[UUID, list[PhotoPayload]]:
    if not card_ids:
        return {}
    rows = db.execute(
        select(BacklogCardPhoto)
        .where(BacklogCardPhoto.card_id.in_(card_ids))
        .order_by(BacklogCardPhoto.card_id, BacklogCardPhoto.sort_order)
    ).scalars()
    grouped: dict[UUID, list[PhotoPayload]] = {}
    for photo in rows:
        grouped.setdefault(photo.card_id, []).append(photo_payload(photo))
    return grouped


def delete_rating_photo(db: Session, photo: LocationRatingPhoto) -> None:
    storage_key = photo.storage_key
    db.delete(photo)
    db.flush()
    _drop_file(storage_key)


def delete_backlog_photo(db: Session, photo: BacklogCardPhoto) -> None:
    storage_key = photo.storage_key
    db.delete(photo)
    db.flush()
    _drop_file(storage_key)
