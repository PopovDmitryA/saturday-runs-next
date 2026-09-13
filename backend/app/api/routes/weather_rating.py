from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.weather_rating import WeatherRatingResponse
from app.services.weather_rating_service import build_weather_rating

# Публичный, как остальные рейтинги: погода и протоколы и без того открыты.
router = APIRouter(prefix="/weather-rating", tags=["weather-rating"])


@router.get("", response_model=WeatherRatingResponse)
def weather_rating(db: Annotated[Session, Depends(get_db)]) -> WeatherRatingResponse:
    return WeatherRatingResponse.model_validate(build_weather_rating(db))
