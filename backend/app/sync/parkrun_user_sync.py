from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import (
    Platform,
    PlatformLink,
    PlatformLinkSyncStatus,
)
from app.platform_adapters.parkrun.url import parse_profile_url
from app.sync.parkrun_participant_import import import_parkrun_participant_activity
from app.sync.user_sync import UserSyncError, _count_participant_runs


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sync_parkrun_platform_link(db: Session, link: PlatformLink, platform: Platform) -> dict[str, object]:
    if platform.code != "parkrun":
        raise UserSyncError(f"Expected parkrun platform, got {platform.code}")

    parsed = parse_profile_url(link.external_url)
    imported = import_parkrun_participant_activity(
        db,
        platform,
        parsed.athlete_id,
        user_id=link.user_id,
    )
    participant = imported.participant
    link.participant_id = participant.id

    link.sync_status = PlatformLinkSyncStatus.ok
    link.error_message = None
    link.last_user_sync_at = _utcnow()

    runs_in_db = _count_participant_runs(db, participant.id)
    missing_runs = None
    if imported.expected_runs is not None and runs_in_db < imported.expected_runs:
        missing_runs = imported.expected_runs - runs_in_db

    return {
        "platform_code": platform.code,
        "participant_id": str(participant.id),
        "barcode_id": participant.barcode_id,
        "runs_in_db": runs_in_db,
        "expected_runs_on_profile": imported.expected_runs,
        "missing_runs": missing_runs,
        "runs_imported": imported.runs_imported,
        "volunteering_imported": imported.volunteering_imported,
    }


def run_parkrun_user_sync(
    db: Session,
    user_id: UUID,
    trigger,
    *,
    platform_link_id: UUID | None = None,
    existing_job=None,
):
    from app.models import (
        Platform,
        PlatformLink,
        SyncJob,
        SyncJobStatus,
        User,
    )
    from app.services.dashboard_service import create_sync_job, recompute_dashboard_cache

    user = db.query(User).filter(User.id == user_id).one_or_none()
    if user is None:
        raise UserSyncError("User not found")

    job = existing_job
    if job is None:
        job = create_sync_job(db, user_id, trigger, platform_link_id=platform_link_id)

    job.status = SyncJobStatus.running
    job.started_at = _utcnow()
    db.commit()

    link_query = (
        db.query(PlatformLink, Platform)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user_id, Platform.code == "parkrun")
    )
    if platform_link_id is not None:
        link_query = link_query.filter(PlatformLink.id == platform_link_id)

    links = link_query.all()
    if not links:
        job.status = SyncJobStatus.success
        job.finished_at = _utcnow()
        recompute_dashboard_cache(db, user_id)
        db.commit()
        return job

    errors: list[str] = []
    for link, platform in links:
        if not platform.is_active:
            continue
        platform_code = platform.code
        link.sync_status = PlatformLinkSyncStatus.syncing
        db.commit()
        try:
            sync_parkrun_platform_link(db, link, platform)
        except Exception as exc:
            db.rollback()
            link = db.query(PlatformLink).filter(PlatformLink.id == link.id).one()
            job = db.query(SyncJob).filter(SyncJob.id == job.id).one()
            errors.append(f"{platform_code}: {exc}")
            link.sync_status = PlatformLinkSyncStatus.error
            link.error_message = str(exc)[:2000]
            db.commit()

    recompute_dashboard_cache(db, user_id)
    job.status = SyncJobStatus.success if not errors else SyncJobStatus.failed
    job.finished_at = _utcnow()
    job.error_message = "; ".join(errors) if errors else None
    db.commit()
    db.refresh(job)
    return job
