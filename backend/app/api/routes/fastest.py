from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas.fastest import FastestRatingResponse, MyFastestRowResponse
from app.services.fastest_rating_service import (
    get_fastest_rating,
    get_my_fastest_row,
)

router = APIRouter(prefix="/fastest", tags=["fastest"])


# Таблица открыта без логина — как остальные рейтинги (решение Дмитрия
# 25.07.2026: локации и рейтинги суть публичная витрина сайта).
@router.get("", response_model=FastestRatingResponse)
def fastest_rating(
    db: Annotated[Session, Depends(get_db)],
    # Неизвестное значение любого фильтра сервис молча приводит к базовому:
    # ссылка со старым или руками набранным параметром должна открывать
    # рейтинг, а не ошибку.
    mode: Annotated[str, Query()] = "results",
    platform: Annotated[str, Query()] = "all",
    gender: Annotated[str, Query()] = "all",
    age_group: Annotated[str, Query()] = "all",
    year: Annotated[str, Query()] = "all",
    # Сколько строк вернуть: срез считается целиком в любом случае, limit лишь
    # укорачивает ответ (карточке на /ratings нужны три строки, не пять тысяч).
    limit: Annotated[int | None, Query(ge=1, le=5000)] = None,
) -> FastestRatingResponse:
    payload = get_fastest_rating(
        db,
        mode=mode,
        platform=platform,
        gender=gender,
        age_group=age_group,
        year=year,
        limit=limit,
    )
    return FastestRatingResponse.model_validate(payload)


# Строка «Вы» — только своя, поэтому логин обязателен.
@router.get("/me", response_model=MyFastestRowResponse)
def my_fastest_row(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    mode: Annotated[str, Query()] = "results",
    platform: Annotated[str, Query()] = "all",
    gender: Annotated[str, Query()] = "all",
    age_group: Annotated[str, Query()] = "all",
    year: Annotated[str, Query()] = "all",
) -> MyFastestRowResponse:
    payload = get_my_fastest_row(
        db, user, mode=mode, platform=platform, gender=gender, age_group=age_group, year=year
    )
    return MyFastestRowResponse.model_validate(payload)
