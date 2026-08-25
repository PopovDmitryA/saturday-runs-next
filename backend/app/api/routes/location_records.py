from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_optional_user
from app.db.session import get_db
from app.models import User
from app.schemas.location_records_rating import LocationRecordsRatingResponse
from app.services.location_records_rating_service import (
    build_location_records_rating,
    viewer_age_group,
)

# Публичный, как и остальные рейтинги: рекорды трасс лежат в открытых
# протоколах систем, логин для их просмотра не нужен.
router = APIRouter(prefix="/location-records", tags=["location-records"])


@router.get("", response_model=LocationRecordsRatingResponse)
def location_records_rating(
    db: Annotated[Session, Depends(get_db)],
    viewer: Annotated[User | None, Depends(get_optional_user)],
    scope: str = "absolute",
    gender: str | None = None,
    age_group: str | None = None,
    platform: str = "all",
) -> LocationRecordsRatingResponse:
    # Незнакомые значения фильтров сервис приводит к базовым (см. normalize_*):
    # старая ссылка должна открывать рейтинг, а не ошибку.
    payload = build_location_records_rating(
        db,
        scope=scope,
        gender=gender,
        age_group=age_group,
        platform=platform,
        viewer_group=viewer_age_group(db, viewer.id if viewer else None),
    )
    return LocationRecordsRatingResponse.model_validate(payload)
