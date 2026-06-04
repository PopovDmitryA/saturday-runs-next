from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Platform, PlatformLink, PlatformLinkSyncStatus, SyncJobTrigger
from app.services.dashboard_service import create_sync_job
from app.services.sync_trigger_service import (
    enqueue_parkrun_user_sync,
    enqueue_s95_user_sync,
    enqueue_user_sync,
)


def _enqueue_platform_link(
    db: Session,
    user_id: UUID,
    trigger: SyncJobTrigger,
    link: PlatformLink,
    platform: Platform,
    *,
    mark_syncing: bool = False,
) -> UUID:
    if mark_syncing:
        link.sync_status = PlatformLinkSyncStatus.syncing
        link.error_message = None
    job = create_sync_job(
        db,
        user_id,
        trigger,
        platform_link_id=link.id,
    )
    if platform.code == "s95":
        enqueue_s95_user_sync(
            user_id,
            trigger,
            job_id=job.id,
            platform_link_id=link.id,
        )
    elif platform.code == "parkrun":
        enqueue_parkrun_user_sync(
            user_id,
            trigger,
            job_id=job.id,
            platform_link_id=link.id,
        )
    elif platform.code == "five_verst":
        enqueue_user_sync(
            user_id,
            trigger,
            job_id=job.id,
            platform_link_id=link.id,
        )
    return job.id


def enqueue_sync_for_platform_codes(
    db: Session,
    user_id: UUID,
    trigger: SyncJobTrigger,
    platform_codes: list[str],
    *,
    mark_syncing: bool = False,
) -> UUID | None:
    """Enqueue sync only for linked platforms whose codes are in platform_codes."""
    if not platform_codes:
        return None

    allowed = set(platform_codes)
    links = (
        db.query(PlatformLink, Platform)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user_id)
        .all()
    )
    first_job_id: UUID | None = None
    for link, platform in links:
        if platform.code not in allowed:
            continue
        job_id = _enqueue_platform_link(
            db, user_id, trigger, link, platform, mark_syncing=mark_syncing
        )
        if first_job_id is None:
            first_job_id = job_id
    return first_job_id


def enqueue_sync_for_all_platforms(
    db: Session,
    user_id: UUID,
    trigger: SyncJobTrigger,
    *,
    mark_syncing: bool = False,
) -> UUID:
    """Create one sync job per linked platform so status and workers do not clash."""
    links = (
        db.query(PlatformLink, Platform)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user_id)
        .all()
    )
    first_job_id: UUID | None = None
    for link, platform in links:
        job_id = _enqueue_platform_link(
            db, user_id, trigger, link, platform, mark_syncing=mark_syncing
        )
        if first_job_id is None:
            first_job_id = job_id
    if first_job_id is None:
        job = create_sync_job(db, user_id, trigger)
        first_job_id = job.id
    return first_job_id


def enqueue_manual_sync_for_all_platforms(db: Session, user_id: UUID) -> UUID:
    return enqueue_sync_for_all_platforms(
        db, user_id, SyncJobTrigger.manual, mark_syncing=True
    )
