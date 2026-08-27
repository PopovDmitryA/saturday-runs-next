from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.location_regions_rating import RegionsRatingResponse
from app.services.location_regions_rating_service import build_regions_rating

# Публичный, как и остальные рейтинги локаций: состав каталога и без того
# открыт — он же лежит на карте.
router = APIRouter(prefix="/location-regions", tags=["location-regions"])


@router.get("", response_model=RegionsRatingResponse)
def location_regions_rating(
    db: Annotated[Session, Depends(get_db)],
    platform: str = "all",
) -> RegionsRatingResponse:
    # Незнакомую систему сервис приводит к общему зачёту (см. normalize_platform).
    payload = build_regions_rating(db, platform=platform)
    return RegionsRatingResponse.model_validate(payload)
