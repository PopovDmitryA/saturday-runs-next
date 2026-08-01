from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas.leaderboards import LeaderboardResponse, MyLeaderboardRowResponse
from app.services.leaderboard_service import (
    LEADERBOARD_GENDERS,
    LEADERBOARD_METRICS,
    MAX_MIN_VISITS,
    LeaderboardMetric,
    get_leaderboard,
    get_my_leaderboard_row,
)

router = APIRouter(prefix="/leaderboards", tags=["leaderboards"])


def _validate_metric(metric: str) -> LeaderboardMetric:
    if metric not in LEADERBOARD_METRICS:
        raise HTTPException(status_code=404, detail="Неизвестный рейтинг")
    return metric  # type: ignore[return-value]


def _validate_gender(gender: str) -> str:
    # Незнакомый пол молча трактуем как «all» — сервис сам игнорирует разрез у
    # метрик без М/Ж (см. _normalize_gender), так что 400 тут был бы избыточен.
    return gender if gender in LEADERBOARD_GENDERS else "all"


# Таблицы рейтингов открыты БЕЗ логина (решение Дмитрия 25.07.2026:
# локации и рейтинги — публичная витрина сайта; до этого с 16.07.2026
# раздел был только для залогиненных).
@router.get("/{metric}", response_model=LeaderboardResponse)
def leaderboard(
    metric: str,
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    gender: str = "all",
    # Порог визитов есть только у туристических рейтингов; фильтр «по одной
    # системе» — у всех, но набор систем зависит от рейтинга и зачёта.
    # Неприменимое значение сервис молча приводит к базовому (см.
    # _normalize_min_visits / _normalize_platform_filter), 400 тут был бы избыточен.
    min_visits: Annotated[int, Query(ge=1, le=MAX_MIN_VISITS)] = 1,
    platform: str = "all",
) -> LeaderboardResponse:
    payload = get_leaderboard(
        db,
        _validate_metric(metric),
        _validate_gender(gender),
        limit=limit,
        min_visits=min_visits,
        platform=platform,
    )
    return LeaderboardResponse.model_validate(payload)


# Строка «Вы» — только своя, поэтому логин обязателен и здесь.
@router.get("/{metric}/me", response_model=MyLeaderboardRowResponse)
def my_leaderboard_row(
    metric: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    gender: str = "all",
    min_visits: Annotated[int, Query(ge=1, le=MAX_MIN_VISITS)] = 1,
    platform: str = "all",
) -> MyLeaderboardRowResponse:
    payload = get_my_leaderboard_row(
        db,
        _validate_metric(metric),
        user,
        _validate_gender(gender),
        min_visits,
        platform,
    )
    return MyLeaderboardRowResponse.model_validate(payload)
