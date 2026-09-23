"""Паспорт трассы локации и рейтинги трасс.

Пока идёт сбор треков, раздел закрыт: отвечаем 404 всем, кроме админа.
Открывается флагом tracks_public_enabled — см. первую итерацию Ч33.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.core.admin import is_admin_user
from app.db.session import get_db
from app.models import User
from app.schemas.course import (
    CourseProfileResponse,
    CourseRatingItem,
    CourseRatingResponse,
    LocationCourseResponse,
)
from app.services.course_rating_service import METRICS, list_course_ratings
from app.services.location_course_service import (
    current_profile,
    has_enough_data,
    profile_history,
)
from app.services.location_page_service import resolve_location_identity

router = APIRouter(tags=["courses"])


def _ensure_visible(user: User, settings: Settings) -> None:
    if settings.tracks_public_enabled or is_admin_user(user, settings):
        return
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


@router.get("/locations/{slug}/course", response_model=LocationCourseResponse)
def get_location_course(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LocationCourseResponse:
    """Паспорт трассы локации: текущая версия и история прежних."""
    _ensure_visible(user, settings)

    identity = resolve_location_identity(db, slug)
    if identity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Локация не найдена")

    # Локация может жить сразу в нескольких системах — берём версию с самым
    # полным набором треков.
    best = None
    history: list[CourseProfileResponse] = []
    for location, _platform_code in identity.locations:
        profile = current_profile(db, location.id)
        if profile is not None and (best is None or profile.tracks_count > best.tracks_count):
            best = profile
            history = [
                CourseProfileResponse.model_validate(item)
                for item in profile_history(db, location.id)
                if not item.is_current
            ]

    return LocationCourseResponse(
        location_slug=identity.slug,
        location_name=identity.name,
        has_data=has_enough_data(best),
        current=CourseProfileResponse.model_validate(best) if has_enough_data(best) else None,
        history=history,
    )


@router.get("/ratings/courses/{metric}", response_model=CourseRatingResponse)
def get_course_rating(
    metric: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CourseRatingResponse:
    """Рейтинг трасс: elevation — перепад высот, straightness — прямолинейность."""
    _ensure_visible(user, settings)
    if metric not in METRICS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Неизвестный рейтинг")

    rows = list_course_ratings(db, metric)
    return CourseRatingResponse(
        metric=metric,
        items=[CourseRatingItem.model_validate(row) for row in rows],
        with_data_count=sum(1 for row in rows if row["has_data"] and row["value"] is not None),
        total_count=len(rows),
    )
