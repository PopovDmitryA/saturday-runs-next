from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas.dashboard import (
    BestResultResponse,
    PersonalRecordResponse,
    RunItemResponse,
    VolunteerRoleStatResponse,
    VolunteeringItemResponse,
)
from app.services.dashboard_service import (
    list_user_best_results,
    list_user_personal_records,
    list_user_runs,
    list_user_volunteer_role_stats,
    list_user_volunteering,
)

router = APIRouter(tags=["runs"])


@router.get("/runs", response_model=list[RunItemResponse])
def list_runs(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_test: Annotated[bool, Query()] = False,
) -> list[RunItemResponse]:
    items = list_user_runs(
        db,
        user.id,
        limit=limit,
        offset=offset,
        include_test_events=include_test,
    )
    return [RunItemResponse.model_validate(item) for item in items]


@router.get("/runs/best-results", response_model=list[BestResultResponse])
def list_best_results(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    include_test: Annotated[bool, Query()] = False,
) -> list[BestResultResponse]:
    items = list_user_best_results(db, user.id, include_test_events=include_test)
    return [BestResultResponse.model_validate(item) for item in items]


@router.get("/runs/personal-records", response_model=list[PersonalRecordResponse])
def list_personal_records(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    include_test: Annotated[bool, Query()] = False,
) -> list[PersonalRecordResponse]:
    items = list_user_personal_records(db, user.id, include_test_events=include_test)
    return [PersonalRecordResponse.model_validate(item) for item in items]


@router.get("/volunteering", response_model=list[VolunteeringItemResponse])
def list_volunteering(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_test: Annotated[bool, Query()] = False,
) -> list[VolunteeringItemResponse]:
    items = list_user_volunteering(
        db,
        user.id,
        limit=limit,
        offset=offset,
        include_test_events=include_test,
    )
    return [VolunteeringItemResponse.model_validate(item) for item in items]


@router.get("/volunteering/role-stats", response_model=list[VolunteerRoleStatResponse])
def list_volunteer_role_stats(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    include_test: Annotated[bool, Query()] = False,
) -> list[VolunteerRoleStatResponse]:
    items = list_user_volunteer_role_stats(db, user.id, include_test_events=include_test)
    return [VolunteerRoleStatResponse.model_validate(item) for item in items]
