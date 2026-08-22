from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas.dashboard import (
    BestResultResponse,
    CoRunnerMeetingResponse,
    CoRunnerResponse,
    PersonalRecordResponse,
    RunItemResponse,
    VolunteeringItemResponse,
    VolunteerRoleStatResponse,
    WinResponse,
)
from app.services.co_runners_service import list_co_runner_meetings, list_co_runners
from app.services.dashboard_service import (
    list_user_best_results,
    list_user_personal_records,
    list_user_runs,
    list_user_volunteer_role_stats,
    list_user_volunteering,
    list_user_wins,
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


@router.get("/runs/co-runners", response_model=list[CoRunnerResponse])
def list_run_co_runners(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    include_test: Annotated[bool, Query()] = False,
) -> list[CoRunnerResponse]:
    items = list_co_runners(db, user.id, include_test_events=include_test, limit=limit)
    return [CoRunnerResponse.model_validate(item) for item in items]


@router.get("/runs/co-runners/{participant_key}/meetings", response_model=list[CoRunnerMeetingResponse])
def list_run_co_runner_meetings(
    participant_key: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    include_test: Annotated[bool, Query()] = False,
) -> list[CoRunnerMeetingResponse]:
    items = list_co_runner_meetings(db, user.id, participant_key, include_test_events=include_test)
    return [CoRunnerMeetingResponse.model_validate(item) for item in items]


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


@router.get("/runs/wins", response_model=list[WinResponse])
def list_wins(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    include_test: Annotated[bool, Query()] = False,
) -> list[WinResponse]:
    items = list_user_wins(db, user.id, include_test_events=include_test)
    return [WinResponse.model_validate(item) for item in items]


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
