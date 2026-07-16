from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.admin import AdminUserPreviewDashboardResponse
from app.schemas.dashboard import (
    BestResultResponse,
    CoRunnerMeetingResponse,
    CoRunnerResponse,
    MyHistoryResponse,
    OnThisDayResponse,
    PersonalRecordResponse,
    RunItemResponse,
    VolunteeringItemResponse,
    VolunteerRoleStatResponse,
)
from app.schemas.locations import CatalogLocationsTableResponse, MapLocationsResponse, UniqueLocationsDetailResponse
from app.services.admin_users_service import (
    get_admin_user_preview_runs,
    get_admin_user_preview_volunteering,
)
from app.services.co_runners_service import list_co_runner_meetings, list_co_runners
from app.services.dashboard_service import (
    list_user_best_results,
    list_user_personal_records,
    list_user_volunteer_role_stats,
)
from app.services.demo_service import (
    get_demo_dashboard,
    get_demo_user,
    get_demo_user_id,
)
from app.services.location_catalog_table_service import build_catalog_locations_table
from app.services.location_map_service import list_catalog_map_locations, list_user_visited_map_locations
from app.services.my_history_service import get_my_history
from app.services.on_this_day_service import get_on_this_day
from app.services.user_unique_locations_detail import build_user_unique_location_details

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get("/dashboard", response_model=AdminUserPreviewDashboardResponse)
def demo_dashboard(db: Annotated[Session, Depends(get_db)]) -> AdminUserPreviewDashboardResponse:
    payload = get_demo_dashboard(db)
    return AdminUserPreviewDashboardResponse.model_validate(payload)


@router.get("/dashboard/on-this-day", response_model=OnThisDayResponse)
def demo_on_this_day(
    db: Annotated[Session, Depends(get_db)],
    date: Annotated[str | None, Query()] = None,
) -> OnThisDayResponse:
    from datetime import date as _date

    user_id = get_demo_user_id(db)
    try:
        today = _date.fromisoformat(date) if date else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="date должен быть в формате YYYY-MM-DD") from exc
    return OnThisDayResponse.model_validate(get_on_this_day(db, user_id, today=today))


@router.get("/dashboard/my-history", response_model=MyHistoryResponse)
def demo_my_history(db: Annotated[Session, Depends(get_db)]) -> MyHistoryResponse:
    user_id = get_demo_user_id(db)
    return MyHistoryResponse.model_validate(get_my_history(db, user_id))


@router.get("/runs", response_model=list[RunItemResponse])
def demo_runs(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[RunItemResponse]:
    user_id = get_demo_user_id(db)
    items = get_admin_user_preview_runs(db, user_id, limit=limit, offset=offset)
    if items is None:
        raise HTTPException(status_code=404, detail="Demo profile not found")
    return [RunItemResponse.model_validate(item) for item in items]


@router.get("/runs/co-runners", response_model=list[CoRunnerResponse])
def demo_co_runners(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[CoRunnerResponse]:
    user_id = get_demo_user_id(db)
    items = list_co_runners(db, user_id, include_test_events=False, limit=limit)
    return [CoRunnerResponse.model_validate(item) for item in items]


@router.get("/runs/co-runners/{participant_key}/meetings", response_model=list[CoRunnerMeetingResponse])
def demo_co_runner_meetings(
    participant_key: str,
    db: Annotated[Session, Depends(get_db)],
) -> list[CoRunnerMeetingResponse]:
    user_id = get_demo_user_id(db)
    items = list_co_runner_meetings(db, user_id, participant_key, include_test_events=False)
    return [CoRunnerMeetingResponse.model_validate(item) for item in items]


@router.get("/runs/best-results", response_model=list[BestResultResponse])
def demo_best_results(db: Annotated[Session, Depends(get_db)]) -> list[BestResultResponse]:
    user_id = get_demo_user_id(db)
    items = list_user_best_results(db, user_id, include_test_events=False)
    return [BestResultResponse.model_validate(item) for item in items]


@router.get("/runs/personal-records", response_model=list[PersonalRecordResponse])
def demo_personal_records(db: Annotated[Session, Depends(get_db)]) -> list[PersonalRecordResponse]:
    user_id = get_demo_user_id(db)
    items = list_user_personal_records(db, user_id, include_test_events=False)
    return [PersonalRecordResponse.model_validate(item) for item in items]


@router.get("/volunteering", response_model=list[VolunteeringItemResponse])
def demo_volunteering(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[VolunteeringItemResponse]:
    user_id = get_demo_user_id(db)
    items = get_admin_user_preview_volunteering(db, user_id, limit=limit, offset=offset)
    if items is None:
        raise HTTPException(status_code=404, detail="Demo profile not found")
    return [VolunteeringItemResponse.model_validate(item) for item in items]


@router.get("/volunteering/role-stats", response_model=list[VolunteerRoleStatResponse])
def demo_volunteer_role_stats(db: Annotated[Session, Depends(get_db)]) -> list[VolunteerRoleStatResponse]:
    user_id = get_demo_user_id(db)
    items = list_user_volunteer_role_stats(db, user_id, include_test_events=False)
    return [VolunteerRoleStatResponse.model_validate(item) for item in items]


@router.get("/locations/visited/map", response_model=MapLocationsResponse)
def demo_visited_map(db: Annotated[Session, Depends(get_db)]) -> MapLocationsResponse:
    user_id = get_demo_user_id(db)
    payload = list_user_visited_map_locations(db, user_id, include_test_events=False)
    return MapLocationsResponse.model_validate(payload)


@router.get("/locations/visited/detail", response_model=UniqueLocationsDetailResponse)
def demo_visited_detail(db: Annotated[Session, Depends(get_db)]) -> UniqueLocationsDetailResponse:
    user_id = get_demo_user_id(db)
    payload = build_user_unique_location_details(db, user_id, include_test_events=False)
    return UniqueLocationsDetailResponse.model_validate(payload)


@router.get("/locations/catalog/map", response_model=MapLocationsResponse)
def demo_catalog_map(db: Annotated[Session, Depends(get_db)]) -> MapLocationsResponse:
    _ = get_demo_user(db)
    payload = list_catalog_map_locations(db)
    return MapLocationsResponse.model_validate(payload)


@router.get("/locations/catalog/table", response_model=CatalogLocationsTableResponse)
def demo_catalog_table(db: Annotated[Session, Depends(get_db)]) -> CatalogLocationsTableResponse:
    user_id = get_demo_user_id(db)
    payload = build_catalog_locations_table(db, user_id, include_test_events=False)
    return CatalogLocationsTableResponse.model_validate(payload)
