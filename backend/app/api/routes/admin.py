from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user
from app.config import get_settings
from app.db.session import get_db
from app.models import User
from app.parkrun.fetch.cdp_session import ParkrunCdpSessionError
from app.schemas.abuse_admin import (
    AbuseBanCreateRequest,
    AbuseBanCreateResponse,
    AbuseBlockListResponse,
    AbuseIpBlockItem,
    AbuseMessageResponse,
    AbuseTelegramBanItem,
)
from app.schemas.admin import (
    AdminS95ParticipantListResponse,
    AdminUserListResponse,
    AdminUserPreviewDashboardResponse,
)
from app.schemas.admin_stats import AdminSiteStatsResponse
from app.schemas.dashboard import (
    BestResultResponse,
    PersonalRecordResponse,
    RunItemResponse,
    SyncRefreshResponse,
    VolunteeringItemResponse,
    VolunteerRoleStatResponse,
)
from app.schemas.locations import CatalogLocationsTableResponse, MapLocationsResponse, UniqueLocationsDetailResponse
from app.schemas.parkrun_admin import (
    ParkrunCdpSaveRequest,
    ParkrunCdpSaveResponse,
    ParkrunLocalWorkerStartRequest,
    ParkrunLocalWorkerStartResponse,
    ParkrunLocalWorkerStatusResponse,
    ParkrunProcessPendingRequest,
    ParkrunProcessPendingResponse,
    ParkrunSessionStatusResponse,
)
from app.services.abuse_admin_service import (
    AbuseAdminError,
    clear_ip_score,
    create_abuse_ban,
    delete_ip_block,
    delete_telegram_ban,
    get_ip_block_details,
    list_abuse_blocks,
)
from app.services.admin_s95_participants_service import search_admin_s95_participants
from app.services.admin_site_stats_service import get_admin_site_stats
from app.services.admin_users_service import (
    get_admin_user,
    get_admin_user_preview_best_results,
    get_admin_user_preview_dashboard,
    get_admin_user_preview_personal_records,
    get_admin_user_preview_runs,
    get_admin_user_preview_volunteer_role_stats,
    get_admin_user_preview_volunteering,
    search_admin_users,
)
from app.services.location_catalog_table_service import build_catalog_locations_table
from app.services.location_map_service import list_user_visited_map_locations
from app.services.parkrun_admin_service import (
    clear_parkrun_captcha_wait,
    get_parkrun_session_status,
    process_parkrun_pending_queue,
    save_parkrun_session_from_chrome,
)
from app.services.parkrun_local_worker import get_local_worker_status, request_local_worker_run
from app.services.profile_fetch_pending_service import reset_failed_parkrun_pending
from app.services.sync_enqueue_service import enqueue_manual_platform_sync
from app.services.user_unique_locations_detail import build_user_unique_location_details

router = APIRouter(prefix="/admin", tags=["admin"])

ADMIN_SUPPORTED_SYNC_PLATFORMS = frozenset({"five_verst", "s95", "parkrun"})
ADMIN_SYNC_QUEUED_MESSAGE = (
    "Запрос на обновление отправлен. Ожидайте исполнения в ближайшее время."
)
ADMIN_SYNC_ALREADY_QUEUED_MESSAGE = (
    "Обновление уже в очереди. Ожидайте исполнения в ближайшее время."
)


def _admin_sync_refresh_response(result) -> SyncRefreshResponse:
    return SyncRefreshResponse(
        job_id=result.job_id,
        status="already_queued" if result.duplicate else "queued",
        message=ADMIN_SYNC_ALREADY_QUEUED_MESSAGE if result.duplicate else ADMIN_SYNC_QUEUED_MESSAGE,
    )


def _handle_abuse_admin_error(exc: AbuseAdminError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _handle_parkrun_admin_error(exc: ParkrunCdpSessionError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/users", response_model=AdminUserListResponse)
def list_admin_users(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    q: Annotated[str | None, Query(max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str, Query(pattern="^(created|runs|volunteering)$")] = "created",
    direction: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> AdminUserListResponse:
    items, total = search_admin_users(
        db, query=q, limit=limit, offset=offset, sort=sort, direction=direction
    )
    return AdminUserListResponse(
        items=items,  # type: ignore[arg-type]
        total=total,
        limit=limit,
        offset=offset,
        query=q,
    )


@router.get("/s95/participants", response_model=AdminS95ParticipantListResponse)
def list_admin_s95_participants(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    q: Annotated[str | None, Query(max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminS95ParticipantListResponse:
    items, total = search_admin_s95_participants(db, query=q, limit=limit, offset=offset)
    return AdminS95ParticipantListResponse(
        items=items,  # type: ignore[arg-type]
        total=total,
        limit=limit,
        offset=offset,
        query=q,
    )


@router.get("/users/{user_id}/preview/dashboard", response_model=AdminUserPreviewDashboardResponse)
def admin_user_preview_dashboard(
    user_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> AdminUserPreviewDashboardResponse:
    payload = get_admin_user_preview_dashboard(db, user_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="User not found")
    return AdminUserPreviewDashboardResponse.model_validate(payload)


@router.get("/users/{user_id}/preview/runs", response_model=list[RunItemResponse])
def admin_user_preview_runs(
    user_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_test: Annotated[bool, Query()] = False,
) -> list[RunItemResponse]:
    items = get_admin_user_preview_runs(db, user_id, limit=limit, offset=offset, include_test=include_test)
    if items is None:
        raise HTTPException(status_code=404, detail="User not found")
    return [RunItemResponse.model_validate(item) for item in items]


@router.get("/users/{user_id}/preview/volunteering", response_model=list[VolunteeringItemResponse])
def admin_user_preview_volunteering(
    user_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_test: Annotated[bool, Query()] = False,
) -> list[VolunteeringItemResponse]:
    items = get_admin_user_preview_volunteering(db, user_id, limit=limit, offset=offset, include_test=include_test)
    if items is None:
        raise HTTPException(status_code=404, detail="User not found")
    return [VolunteeringItemResponse.model_validate(item) for item in items]


@router.get("/users/{user_id}/preview/runs/best-results", response_model=list[BestResultResponse])
def admin_user_preview_best_results(
    user_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    include_test: Annotated[bool, Query()] = False,
) -> list[BestResultResponse]:
    items = get_admin_user_preview_best_results(db, user_id, include_test_events=include_test)
    if items is None:
        raise HTTPException(status_code=404, detail="User not found")
    return [BestResultResponse.model_validate(item) for item in items]


@router.get("/users/{user_id}/preview/runs/personal-records", response_model=list[PersonalRecordResponse])
def admin_user_preview_personal_records(
    user_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    include_test: Annotated[bool, Query()] = False,
) -> list[PersonalRecordResponse]:
    items = get_admin_user_preview_personal_records(db, user_id, include_test_events=include_test)
    if items is None:
        raise HTTPException(status_code=404, detail="User not found")
    return [PersonalRecordResponse.model_validate(item) for item in items]


@router.get(
    "/users/{user_id}/preview/volunteering/role-stats",
    response_model=list[VolunteerRoleStatResponse],
)
def admin_user_preview_volunteer_role_stats(
    user_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    include_test: Annotated[bool, Query()] = False,
) -> list[VolunteerRoleStatResponse]:
    items = get_admin_user_preview_volunteer_role_stats(db, user_id, include_test_events=include_test)
    if items is None:
        raise HTTPException(status_code=404, detail="User not found")
    return [VolunteerRoleStatResponse.model_validate(item) for item in items]


@router.post(
    "/users/{user_id}/sync/{platform_code}",
    response_model=SyncRefreshResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def admin_user_sync_platform(
    user_id: UUID,
    platform_code: str,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> SyncRefreshResponse:
    if platform_code not in ADMIN_SUPPORTED_SYNC_PLATFORMS:
        raise HTTPException(status_code=404, detail="Unknown platform")
    user = get_admin_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        result = enqueue_manual_platform_sync(db, user, platform_code)
    except ValueError:
        raise HTTPException(status_code=404, detail="Platform link not found") from None
    db.commit()
    return _admin_sync_refresh_response(result)


@router.get("/users/{user_id}/preview/locations/visited/map", response_model=MapLocationsResponse)
def admin_user_preview_visited_map(
    user_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    include_test: Annotated[bool, Query()] = False,
) -> MapLocationsResponse:
    if get_admin_user(db, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    payload = list_user_visited_map_locations(db, user_id, include_test_events=include_test)
    return MapLocationsResponse.model_validate(payload)


@router.get("/users/{user_id}/preview/locations/visited/detail", response_model=UniqueLocationsDetailResponse)
def admin_user_preview_visited_detail(
    user_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    include_test: Annotated[bool, Query()] = False,
) -> UniqueLocationsDetailResponse:
    if get_admin_user(db, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    payload = build_user_unique_location_details(db, user_id, include_test_events=include_test)
    return UniqueLocationsDetailResponse.model_validate(payload)


@router.get("/users/{user_id}/preview/locations/catalog/table", response_model=CatalogLocationsTableResponse)
def admin_user_preview_catalog_table(
    user_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    include_test: Annotated[bool, Query()] = False,
) -> CatalogLocationsTableResponse:
    if get_admin_user(db, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    payload = build_catalog_locations_table(db, user_id, include_test_events=include_test)
    return CatalogLocationsTableResponse.model_validate(payload)


@router.get("/abuse/blocks", response_model=AbuseBlockListResponse)
def admin_list_abuse_blocks(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> AbuseBlockListResponse:
    payload = list_abuse_blocks(db)
    return AbuseBlockListResponse(
        ip_blocks=[AbuseIpBlockItem.model_validate(item) for item in payload["ip_blocks"]],
        telegram_bans=[AbuseTelegramBanItem.model_validate(item) for item in payload["telegram_bans"]],
    )


@router.post("/abuse/blocks", response_model=AbuseBanCreateResponse)
def admin_create_abuse_ban(
    body: AbuseBanCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(get_current_admin_user)],
) -> AbuseBanCreateResponse:
    try:
        payload = create_abuse_ban(
            db,
            admin,
            get_settings(),
            target=body.target,
            duration_seconds=body.duration_seconds,
            reason=body.reason,
            ban_ip=body.ban_ip,
            ban_account=body.ban_account,
        )
    except AbuseAdminError as exc:
        raise _handle_abuse_admin_error(exc) from exc
    return AbuseBanCreateResponse.model_validate(payload)


@router.delete("/abuse/blocks/ip/{ip}", response_model=AbuseMessageResponse)
def admin_delete_ip_block(
    ip: str,
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> AbuseMessageResponse:
    try:
        delete_ip_block(ip)
    except AbuseAdminError as exc:
        raise _handle_abuse_admin_error(exc) from exc
    return AbuseMessageResponse(message="ip_unblocked")


@router.delete("/abuse/blocks/telegram/{telegram_id}", response_model=AbuseMessageResponse)
def admin_delete_telegram_ban(
    telegram_id: int,
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> AbuseMessageResponse:
    try:
        delete_telegram_ban(telegram_id, get_settings())
    except AbuseAdminError as exc:
        raise _handle_abuse_admin_error(exc) from exc
    return AbuseMessageResponse(message="telegram_unblocked")


@router.post("/abuse/blocks/ip/{ip}/clear-score", response_model=AbuseMessageResponse)
def admin_clear_ip_abuse_score(
    ip: str,
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> AbuseMessageResponse:
    try:
        clear_ip_score(ip)
    except AbuseAdminError as exc:
        raise _handle_abuse_admin_error(exc) from exc
    return AbuseMessageResponse(message="score_cleared")


@router.get("/abuse/blocks/ip/{ip}", response_model=AbuseIpBlockItem)
def admin_get_ip_block(
    ip: str,
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> AbuseIpBlockItem:
    try:
        payload = get_ip_block_details(ip)
    except AbuseAdminError as exc:
        raise _handle_abuse_admin_error(exc) from exc
    return AbuseIpBlockItem.model_validate(payload)


@router.get("/stats", response_model=AdminSiteStatsResponse)
def admin_site_stats(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    period_days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> AdminSiteStatsResponse:
    payload = get_admin_site_stats(db, period_days=period_days)
    return AdminSiteStatsResponse.model_validate(payload)


@router.get("/parkrun/session", response_model=ParkrunSessionStatusResponse)
def admin_parkrun_session_status(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> ParkrunSessionStatusResponse:
    return ParkrunSessionStatusResponse.model_validate(get_parkrun_session_status(db))


@router.post("/parkrun/save-session", response_model=ParkrunCdpSaveResponse)
def admin_parkrun_save_session(
    body: ParkrunCdpSaveRequest,
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> ParkrunCdpSaveResponse:
    try:
        payload = save_parkrun_session_from_chrome(body.cdp_url or None)
    except ParkrunCdpSessionError as exc:
        raise _handle_parkrun_admin_error(exc) from exc
    return ParkrunCdpSaveResponse.model_validate(payload)


@router.post("/parkrun/clear-captcha-wait")
def admin_parkrun_clear_captcha_wait(
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> dict[str, str]:
    clear_parkrun_captcha_wait()
    return {"message": "captcha_wait_cleared"}


@router.post("/parkrun/process-pending", response_model=ParkrunProcessPendingResponse)
def admin_parkrun_process_pending(
    body: ParkrunProcessPendingRequest,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> ParkrunProcessPendingResponse:
    try:
        payload = process_parkrun_pending_queue(db, limit=body.limit)
    except ParkrunCdpSessionError as exc:
        raise _handle_parkrun_admin_error(exc) from exc
    return ParkrunProcessPendingResponse.model_validate(payload)


@router.post("/parkrun/reset-failed-pending")
def admin_parkrun_reset_failed_pending(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> dict[str, int | str]:
    count = reset_failed_parkrun_pending(db)
    return {"reset": count, "message": f"В очередь возвращено профилей: {count}"}


@router.get("/parkrun/local-worker", response_model=ParkrunLocalWorkerStatusResponse)
def admin_parkrun_local_worker_status(
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> ParkrunLocalWorkerStatusResponse:
    return ParkrunLocalWorkerStatusResponse.model_validate(get_local_worker_status())


@router.post("/parkrun/local-worker/start", response_model=ParkrunLocalWorkerStartResponse)
def admin_parkrun_local_worker_start(
    body: ParkrunLocalWorkerStartRequest,
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> ParkrunLocalWorkerStartResponse:
    payload = request_local_worker_run(reset_failed=body.reset_failed)
    return ParkrunLocalWorkerStartResponse.model_validate(payload)
