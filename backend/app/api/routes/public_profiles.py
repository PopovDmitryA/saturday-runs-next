from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_optional_user
from app.config import Settings, get_settings
from app.core.admin import is_admin_user
from app.models import User
from app.schemas.admin import AdminUserPreviewDashboardResponse
from app.schemas.dashboard import MyHistoryResponse, RunItemResponse, VolunteeringItemResponse
from app.schemas.locations import CatalogLocationsTableResponse, MapLocationsResponse, UniqueLocationsDetailResponse
from app.services.admin_users_service import (
    get_admin_user_preview_best_results,
    get_admin_user_preview_dashboard,
    get_admin_user_preview_personal_records,
    get_admin_user_preview_volunteer_role_stats,
)
from app.services.dashboard_service import list_user_runs, list_user_volunteering
from app.services.location_catalog_table_service import build_catalog_locations_table
from app.services.location_map_service import list_user_visited_map_locations
from app.services.my_history_service import get_my_history
from app.services.profile_slug_service import resolve_profile_handle
from app.services.user_unique_locations_detail import build_user_unique_location_details

router = APIRouter(prefix="/users", tags=["public-profiles"])


class ProfileHandleResolveResponse(BaseModel):
    serial_id: int
    display_name: str | None = None


@router.get("/resolve/{handle}", response_model=ProfileHandleResolveResponse)
def resolve_public_profile_handle(
    handle: str,
    db: Annotated[Session, Depends(get_db)],
) -> ProfileHandleResolveResponse:
    """Резолв vanity-ссылки (slug) или числового serial_id в serial_id профиля.

    Доступ к самим данным профиля проверяется на конкретных эндпоинтах ниже
    (приватный профиль → 403), поэтому резолв не раскрывает приватные данные —
    только сопоставляет хэндл с serial_id, как и числовой роут.
    """
    target = resolve_profile_handle(db, handle)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    return ProfileHandleResolveResponse(serial_id=target.serial_id, display_name=target.display_name)


def _resolve_user(serial_id: int, db: Session) -> User:
    target = db.query(User).filter(User.serial_id == serial_id).one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    return target


def _check_access(target: User, requester: User | None, settings: Settings) -> None:
    if not target.profile_private:
        return
    if requester is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Профиль скрыт пользователем")
    if str(requester.id) != str(target.id) and not is_admin_user(requester, settings):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Профиль скрыт пользователем")


def _get_user_uuid(serial_id: int, db: Session, requester: User | None, settings: Settings) -> UUID:
    target = _resolve_user(serial_id, db)
    _check_access(target, requester, settings)
    return target.id


@router.get("/{serial_id}/profile/dashboard", response_model=AdminUserPreviewDashboardResponse)
def public_profile_dashboard(
    serial_id: int,
    db: Annotated[Session, Depends(get_db)],
    requester: Annotated[User | None, Depends(get_optional_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AdminUserPreviewDashboardResponse:
    user_id = _get_user_uuid(serial_id, db, requester, settings)
    payload = get_admin_user_preview_dashboard(db, user_id)
    return AdminUserPreviewDashboardResponse.model_validate(payload)


@router.get("/{serial_id}/profile/runs", response_model=list[RunItemResponse])
def public_profile_runs(
    serial_id: int,
    db: Annotated[Session, Depends(get_db)],
    requester: Annotated[User | None, Depends(get_optional_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    limit: int = 200,
    offset: int = 0,
    include_test: bool = False,
) -> list[RunItemResponse]:
    user_id = _get_user_uuid(serial_id, db, requester, settings)
    items = list_user_runs(db, user_id, limit=limit, offset=offset, include_test_events=include_test)
    return [RunItemResponse.model_validate(i) for i in items]


@router.get("/{serial_id}/profile/history", response_model=MyHistoryResponse)
def public_profile_history(
    serial_id: int,
    db: Annotated[Session, Depends(get_db)],
    requester: Annotated[User | None, Depends(get_optional_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    include_test: bool = False,
) -> MyHistoryResponse:
    user_id = _get_user_uuid(serial_id, db, requester, settings)
    return MyHistoryResponse.model_validate(get_my_history(db, user_id, include_test_events=include_test))


@router.get("/{serial_id}/profile/volunteering", response_model=list[VolunteeringItemResponse])
def public_profile_volunteering(
    serial_id: int,
    db: Annotated[Session, Depends(get_db)],
    requester: Annotated[User | None, Depends(get_optional_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    limit: int = 200,
    offset: int = 0,
    include_test: bool = False,
) -> list[VolunteeringItemResponse]:
    user_id = _get_user_uuid(serial_id, db, requester, settings)
    items = list_user_volunteering(db, user_id, limit=limit, offset=offset, include_test_events=include_test)
    return [VolunteeringItemResponse.model_validate(i) for i in items]


@router.get("/{serial_id}/profile/runs/best-results")
def public_profile_best_results(
    serial_id: int,
    include_test: bool = False,
    db: Session = Depends(get_db),
    requester: User | None = Depends(get_optional_user),
    settings: Settings = Depends(get_settings),
):
    user_id = _get_user_uuid(serial_id, db, requester, settings)
    return get_admin_user_preview_best_results(db, user_id, include_test_events=include_test)


@router.get("/{serial_id}/profile/runs/personal-records")
def public_profile_personal_records(
    serial_id: int,
    include_test: bool = False,
    db: Session = Depends(get_db),
    requester: User | None = Depends(get_optional_user),
    settings: Settings = Depends(get_settings),
):
    user_id = _get_user_uuid(serial_id, db, requester, settings)
    return get_admin_user_preview_personal_records(db, user_id, include_test_events=include_test)


@router.get("/{serial_id}/profile/volunteering/role-stats")
def public_profile_volunteer_role_stats(
    serial_id: int,
    include_test: bool = False,
    db: Session = Depends(get_db),
    requester: User | None = Depends(get_optional_user),
    settings: Settings = Depends(get_settings),
):
    user_id = _get_user_uuid(serial_id, db, requester, settings)
    return get_admin_user_preview_volunteer_role_stats(db, user_id, include_test_events=include_test)


@router.get("/{serial_id}/profile/locations/visited/map", response_model=MapLocationsResponse)
def public_profile_visited_map(
    serial_id: int,
    include_test: bool = False,
    db: Session = Depends(get_db),
    requester: User | None = Depends(get_optional_user),
    settings: Settings = Depends(get_settings),
) -> MapLocationsResponse:
    user_id = _get_user_uuid(serial_id, db, requester, settings)
    payload = list_user_visited_map_locations(db, user_id, include_test_events=include_test)
    return MapLocationsResponse.model_validate(payload)


@router.get("/{serial_id}/profile/locations/visited/detail", response_model=UniqueLocationsDetailResponse)
def public_profile_visited_detail(
    serial_id: int,
    include_test: bool = False,
    db: Session = Depends(get_db),
    requester: User | None = Depends(get_optional_user),
    settings: Settings = Depends(get_settings),
) -> UniqueLocationsDetailResponse:
    user_id = _get_user_uuid(serial_id, db, requester, settings)
    payload = build_user_unique_location_details(db, user_id, include_test_events=include_test)
    return UniqueLocationsDetailResponse.model_validate(payload)


@router.get("/{serial_id}/profile/locations/catalog/table", response_model=CatalogLocationsTableResponse)
def public_profile_catalog_table(
    serial_id: int,
    include_test: bool = False,
    db: Session = Depends(get_db),
    requester: User | None = Depends(get_optional_user),
    settings: Settings = Depends(get_settings),
) -> CatalogLocationsTableResponse:
    user_id = _get_user_uuid(serial_id, db, requester, settings)
    payload = build_catalog_locations_table(db, user_id, include_test_events=include_test)
    return CatalogLocationsTableResponse.model_validate(payload)
