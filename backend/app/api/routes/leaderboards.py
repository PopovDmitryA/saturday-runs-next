from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user, get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas.leaderboards import LeaderboardResponse, MyLeaderboardRowResponse
from app.services.leaderboard_service import (
    LEADERBOARD_METRICS,
    LeaderboardMetric,
    get_leaderboard,
    get_my_leaderboard_row,
)

# ВРЕМЕННО до публичного запуска: раздел закрыт под админа (get_current_admin_user).
# При открытии на всех — убрать зависимость с leaderboard(), /me оставить на get_current_user.
router = APIRouter(prefix="/leaderboards", tags=["leaderboards"])


def _validate_metric(metric: str) -> LeaderboardMetric:
    if metric not in LEADERBOARD_METRICS:
        raise HTTPException(status_code=404, detail="Неизвестный рейтинг")
    return metric  # type: ignore[return-value]


@router.get("/{metric}", response_model=LeaderboardResponse)
def leaderboard(
    metric: str,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> LeaderboardResponse:
    payload = get_leaderboard(db, _validate_metric(metric), limit=limit)
    return LeaderboardResponse.model_validate(payload)


@router.get("/{metric}/me", response_model=MyLeaderboardRowResponse)
def my_leaderboard_row(
    metric: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> MyLeaderboardRowResponse:
    payload = get_my_leaderboard_row(db, _validate_metric(metric), user)
    return MyLeaderboardRowResponse.model_validate(payload)
