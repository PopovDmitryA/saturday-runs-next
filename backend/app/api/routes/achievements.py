from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas.achievements import (
    AchievementsResponse,
    GoalsResponse,
    GoalsUpdateRequest,
    StartNumberPlanResponse,
)
from app.services.achievements_service import (
    GoalValidationError,
    StartNumberPlanError,
    build_start_numbers_plan,
    compute_challenges,
    get_goals_payload,
    save_goals,
)

router = APIRouter(prefix="/achievements", tags=["achievements"])


@router.get("", response_model=AchievementsResponse)
def get_achievements(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    platform: Annotated[str | None, Query(description="Сузить челленджи до одной системы")] = None,
) -> AchievementsResponse:
    return AchievementsResponse.model_validate(compute_challenges(db, user.id, platform_code=platform))


@router.get("/start-numbers-plan", response_model=StartNumberPlanResponse)
def get_start_numbers_plan(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    code: Annotated[str, Query(description="Код челленджа: start_numbers | start_numbers_pro")],
    platform: Annotated[str | None, Query(description="Сузить план до одной системы")] = None,
) -> StartNumberPlanResponse:
    try:
        payload = build_start_numbers_plan(db, user.id, code=code, platform_code=platform)
    except StartNumberPlanError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return StartNumberPlanResponse.model_validate(payload)


@router.get("/goals", response_model=GoalsResponse)
def get_goals(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> GoalsResponse:
    return GoalsResponse.model_validate(get_goals_payload(db, user.id))


@router.put("/goals", response_model=GoalsResponse)
def update_goals(
    payload: GoalsUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> GoalsResponse:
    try:
        result = save_goals(
            db,
            user.id,
            [(goal.goal_type, goal.target_value) for goal in payload.goals],
        )
    except GoalValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return GoalsResponse.model_validate(result)
