from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user, get_current_user
from app.config import Settings, get_settings
from app.core.rate_limit import check_rate_limit
from app.db.session import get_db
from app.models import Platform, PlatformLink, PlatformLinkSyncStatus, SyncJobTrigger, User
from app.schemas.dashboard import SyncQueueResponse, SyncRefreshResponse, SyncStatusResponse
from app.services.dashboard_service import create_sync_job, get_sync_status_payload
from app.services.sync_enqueue_service import enqueue_manual_sync_for_all_platforms
from app.services.sync_trigger_service import (
    enqueue_parkrun_user_sync,
    enqueue_s95_user_sync,
    enqueue_user_sync,
)
from app.services.task_queue_service import get_admin_task_queue_payload

router = APIRouter(prefix="/sync", tags=["sync"])

SUPPORTED_SYNC_PLATFORMS = frozenset({"five_verst", "s95", "parkrun"})


def _enqueue_platform_sync(
    db: Session,
    user: User,
    platform_code: str,
    *,
    job_id,
) -> None:
    link = (
        db.query(PlatformLink)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user.id, Platform.code == platform_code)
        .one_or_none()
    )
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Platform link not found")

    if platform_code == "s95":
        enqueue_s95_user_sync(
            user.id,
            SyncJobTrigger.manual,
            job_id=job_id,
            platform_link_id=link.id,
        )
    elif platform_code == "parkrun":
        enqueue_parkrun_user_sync(
            user.id,
            SyncJobTrigger.manual,
            job_id=job_id,
            platform_link_id=link.id,
        )
    else:
        enqueue_user_sync(
            user.id,
            SyncJobTrigger.manual,
            job_id=job_id,
            platform_link_id=link.id,
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

    job_id = enqueue_manual_sync_for_all_platforms(db, user.id)
    db.commit()
    return SyncRefreshResponse(job_id=job_id, status="queued")


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

    link = (
        db.query(PlatformLink)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user.id, Platform.code == platform_code)
        .one_or_none()
    )
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Platform link not found")

    link.sync_status = PlatformLinkSyncStatus.syncing
    link.error_message = None
    job = create_sync_job(db, user.id, SyncJobTrigger.manual, platform_link_id=link.id)
    db.commit()
    _enqueue_platform_sync(db, user, platform_code, job_id=job.id)
    return SyncRefreshResponse(job_id=job.id, status="queued")
