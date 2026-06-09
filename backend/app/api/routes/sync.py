from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user, get_current_user
from app.config import Settings, get_settings
from app.core.rate_limit import check_rate_limit
from app.db.session import get_db
from app.models import User
from app.schemas.dashboard import SyncQueueResponse, SyncRefreshResponse, SyncStatusResponse
from app.services.dashboard_service import get_sync_status_payload
from app.services.sync_enqueue_service import (
    enqueue_manual_platform_sync,
    enqueue_manual_sync_for_all_platforms,
)
from app.services.task_queue_service import get_admin_task_queue_payload

router = APIRouter(prefix="/sync", tags=["sync"])

SUPPORTED_SYNC_PLATFORMS = frozenset({"five_verst", "s95", "parkrun"})
SYNC_REFRESH_QUEUED_MESSAGE = (
    "Запрос на обновление отправлен. Ожидайте исполнения в ближайшее время."
)
SYNC_REFRESH_ALREADY_QUEUED_MESSAGE = (
    "Обновление уже в очереди. Ожидайте исполнения в ближайшее время."
)


def _refresh_response(result) -> SyncRefreshResponse:
    return SyncRefreshResponse(
        job_id=result.job_id,
        status="already_queued" if result.duplicate else "queued",
        message=SYNC_REFRESH_ALREADY_QUEUED_MESSAGE if result.duplicate else SYNC_REFRESH_QUEUED_MESSAGE,
    )


@router.get("/status", response_model=SyncStatusResponse)
def sync_status(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> SyncStatusResponse:
    payload = get_sync_status_payload(db, user.id)
    return SyncStatusResponse.model_validate(payload)


@router.get("/queue", response_model=SyncQueueResponse)
def sync_queue(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> SyncQueueResponse:
    payload = get_admin_task_queue_payload(db)
    return SyncQueueResponse.model_validate(payload)


@router.post("/refresh", response_model=SyncRefreshResponse, status_code=status.HTTP_202_ACCEPTED)
def sync_refresh(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SyncRefreshResponse:
    rate_key = f"sync_refresh:{user.id}"
    allowed = check_rate_limit(
        rate_key,
        settings.sync_refresh_rate_limit_per_user,
        settings.sync_refresh_rate_limit_window_seconds,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Sync refresh rate limit exceeded (1 per 30 minutes)",
        )

    result = enqueue_manual_sync_for_all_platforms(db, user.id)
    db.commit()
    return _refresh_response(result)


@router.post("/refresh/{platform_code}", response_model=SyncRefreshResponse, status_code=status.HTTP_202_ACCEPTED)
def sync_refresh_platform(
    platform_code: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SyncRefreshResponse:
    if platform_code not in SUPPORTED_SYNC_PLATFORMS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown platform")

    rate_key = f"sync_refresh:{user.id}:{platform_code}"
    allowed = check_rate_limit(
        rate_key,
        settings.sync_refresh_rate_limit_per_user,
        settings.sync_refresh_rate_limit_window_seconds,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Sync refresh rate limit exceeded (1 per 30 minutes)",
        )

    try:
        result = enqueue_manual_platform_sync(db, user, platform_code)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Platform link not found") from None
    db.commit()
    return _refresh_response(result)
