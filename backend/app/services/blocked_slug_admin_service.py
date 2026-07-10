from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models import BlockedProfileSlug, User
from app.services.profile_slug_service import SlugError, validate_slug


class BlockedSlugError(ValueError):
    """Ошибка управления блок-листом slug'ов (валидация/дубликат/не найдено)."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def list_blocked_slugs(db: Session) -> list[BlockedProfileSlug]:
    return (
        db.query(BlockedProfileSlug)
        .order_by(BlockedProfileSlug.created_at.desc(), BlockedProfileSlug.slug)
        .all()
    )


def create_blocked_slug(
    db: Session,
    admin: User,
    *,
    raw_slug: str,
    comment: str | None,
) -> BlockedProfileSlug:
    try:
        slug = validate_slug(raw_slug)
    except SlugError as exc:
        raise BlockedSlugError(str(exc)) from exc

    exists = (
        db.query(BlockedProfileSlug.id)
        .filter(BlockedProfileSlug.slug == slug)
        .first()
    )
    if exists is not None:
        raise BlockedSlugError("Эта ссылка уже в блок-листе", status_code=409)

    normalized_comment = (comment or "").strip() or None
    entry = BlockedProfileSlug(
        slug=slug,
        comment=normalized_comment,
        created_by_user_id=admin.id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def delete_blocked_slug(db: Session, entry_id: UUID) -> None:
    entry = (
        db.query(BlockedProfileSlug)
        .filter(BlockedProfileSlug.id == entry_id)
        .one_or_none()
    )
    if entry is None:
        raise BlockedSlugError("Запись не найдена", status_code=404)
    db.delete(entry)
    db.commit()
