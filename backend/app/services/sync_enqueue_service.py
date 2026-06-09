from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Platform, PlatformLink, PlatformLinkSyncStatus, SyncJobTrigger, User
from app.services.dashboard_service import create_sync_job
from app.services.sync_dedup_service import (
    SyncEnqueueResult,
    find_active_sync_job,
    user_sync_is_already_pending,
)
from app.services.sync_trigger_service import (
    enqueue_parkrun_user_sync,
    enqueue_s95_user_sync,
    enqueue_user_sync,
)


def _dispatch_platform_sync(
    user_id: UUID,
    trigger: SyncJobTrigger,
    platform: Platform,
    *,
    job_id: UUID,
    platform_link_id: UUID,
) -> None:
    if platform.code == "s95":
        enqueue_s95_user_sync(
            user_id,
            trigger,
            job_id=job_id,
            platform_link_id=platform_link_id,
        )
    elif platform.code == "parkrun":
        enqueue_parkrun_user_sync(
            user_id,
            trigger,
            job_id=job_id,
            platform_link_id=platform_link_id,
        )
    elif platform.code == "five_verst":
        enqueue_user_sync(
            user_id,
            trigger,
            job_id=job_id,
            platform_link_id=platform_link_id,
        )


def _enqueue_platform_link(
    db: Session,
    user_id: UUID,
    trigger: SyncJobTrigger,
    link: PlatformLink,
    platform: Platform,
    *,
    mark_syncing: bool = False,
) -> SyncEnqueueResult:
    existing = user_sync_is_already_pending(
        db,
        user_id=user_id,
        platform_link_id=link.id,
        platform_code=platform.code,
    )
    if existing is not None:
        if mark_syncing:
            link.sync_status = PlatformLinkSyncStatus.syncing
            link.error_message = None
        return SyncEnqueueResult(job_id=existing.id, duplicate=True)

    stale_job = find_active_sync_job(db, user_id, link.id)
    if stale_job is not None:
        if mark_syncing:
            link.sync_status = PlatformLinkSyncStatus.syncing
            link.error_message = None
        _dispatch_platform_sync(
            user_id,
            trigger,
            platform,
            job_id=stale_job.id,
            platform_link_id=link.id,
        )
        return SyncEnqueueResult(job_id=stale_job.id, duplicate=False)

    if mark_syncing:
        link.sync_status = PlatformLinkSyncStatus.syncing
        link.error_message = None
    job = create_sync_job(
        db,
        user_id,
        trigger,
        platform_link_id=link.id,
    )
    _dispatch_platform_sync(
        user_id,
        trigger,
        platform,
        job_id=job.id,
        platform_link_id=link.id,
    )
    return SyncEnqueueResult(job_id=job.id, duplicate=False)


def enqueue_sync_for_platform_codes(
    db: Session,
    user_id: UUID,
    trigger: SyncJobTrigger,
    platform_codes: list[str],
    *,
    mark_syncing: bool = False,
) -> SyncEnqueueResult | None:
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
    first_result: SyncEnqueueResult | None = None
    all_duplicate = True
    processed = 0
    for link, platform in links:
        if platform.code not in allowed:
            continue
        processed += 1
        result = _enqueue_platform_link(
            db, user_id, trigger, link, platform, mark_syncing=mark_syncing
        )
        if first_result is None:
            first_result = result
        if not result.duplicate:
            all_duplicate = False
    if first_result is None:
        return None
    return SyncEnqueueResult(job_id=first_result.job_id, duplicate=all_duplicate and processed > 0)


def enqueue_sync_for_all_platforms(
    db: Session,
    user_id: UUID,
    trigger: SyncJobTrigger,
    *,
    mark_syncing: bool = False,
) -> SyncEnqueueResult:
    """Create one sync job per linked platform so status and workers do not clash."""
    links = (
        db.query(PlatformLink, Platform)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user_id)
        .all()
    )
    first_result: SyncEnqueueResult | None = None
    all_duplicate = True
    for link, platform in links:
        result = _enqueue_platform_link(
            db, user_id, trigger, link, platform, mark_syncing=mark_syncing
        )
        if first_result is None:
            first_result = result
        if not result.duplicate:
            all_duplicate = False
    if first_result is None:
        job = create_sync_job(db, user_id, trigger)
        return SyncEnqueueResult(job_id=job.id, duplicate=False)
    return SyncEnqueueResult(job_id=first_result.job_id, duplicate=all_duplicate)


def enqueue_linking_platform_sync(
    db: Session,
    user_id: UUID,
    link: PlatformLink,
    platform: Platform,
) -> SyncEnqueueResult:
    return _enqueue_platform_link(
        db,
        user_id,
        SyncJobTrigger.linking,
        link,
        platform,
        mark_syncing=False,
    )


def enqueue_manual_sync_for_all_platforms(db: Session, user_id: UUID) -> SyncEnqueueResult:
    return enqueue_sync_for_all_platforms(
        db, user_id, SyncJobTrigger.manual, mark_syncing=True
    )


def enqueue_manual_platform_sync(
    db: Session,
    user: User,
    platform_code: str,
) -> SyncEnqueueResult:
    row = (
        db.query(PlatformLink, Platform)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user.id, Platform.code == platform_code)
        .one_or_none()
    )
    if row is None:
        raise ValueError(f"Platform link not found: {platform_code}")
    link, platform = row
    return _enqueue_platform_link(
        db,
        user.id,
        SyncJobTrigger.manual,
        link,
        platform,
        mark_syncing=True,
    )
