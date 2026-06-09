from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import (
    Participant,
    Platform,
    PlatformLink,
    PlatformLinkSyncStatus,
)
from app.platform_adapters.s95 import parser as s95_parser
from app.platform_adapters.s95.url import parse_athlete_url
from app.sync import upsert as sync_upsert
from app.sync.s95_athlete_mismatch import (
    refetch_mismatched_protocols,
    refetch_protocols_for_profile_volunteering,
)
from app.sync.s95_participant_sync import apply_s95_participant_profile
from app.sync.user_sync import UserSyncError, _count_participant_runs


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _apply_s95_participant(db: Session, platform: Platform, profile) -> Participant:
    row = sync_upsert.upsert_participant(
        db,
        platform,
        external_user_id=profile.external_user_id,
        display_name=profile.display_name,
        profile_url=profile.profile_url,
    )
    apply_s95_participant_profile(row, profile, parser_version=sync_upsert.PARSER_VERSION)
    db.flush()
    return row


def sync_s95_platform_link(db: Session, link: PlatformLink, platform: Platform) -> dict[str, object]:
    if platform.code != "s95":
        raise UserSyncError(f"Expected s95 platform, got {platform.code}")

    parsed = parse_athlete_url(link.external_url)
    profile, runs, volunteering = s95_parser.fetch_athlete_activity(
        parsed.external_user_id,
        domain=parsed.domain,
    )

    participant = _apply_s95_participant(db, platform, profile)
    link.participant_id = participant.id

    runs_imported = sync_upsert.import_profile_run_results(db, platform, runs)
    volunteering_imported = sync_upsert.import_profile_volunteer_results(
        db, platform, participant.id, volunteering
    )

    from app.config import get_settings
    from app.sync.profile_protocol_queue import enqueue_missing_protocols_for_profile

    settings = get_settings()
    protocol_enqueue = enqueue_missing_protocols_for_profile(
        db,
        platform,
        runs,
        volunteering,
        limit=settings.s95_athlete_mismatch_check_runs,
    )
    refetch_result = refetch_mismatched_protocols(
        db,
        platform,
        participant,
        runs,
        limit=settings.s95_athlete_mismatch_check_runs,
    )
    vol_refetch = refetch_protocols_for_profile_volunteering(
        db,
        platform,
        volunteering,
        limit=settings.s95_athlete_mismatch_check_runs,
    )
    refetch_result.protocols_refetched += vol_refetch.protocols_refetched
    if vol_refetch.errors:
        refetch_result.errors.extend(vol_refetch.errors)

    from app.sync.parkrun_participant_discovery import discover_parkrun_participant_from_barcode

    parkrun_discovery = discover_parkrun_participant_from_barcode(db, profile.barcode_id)

    link.sync_status = PlatformLinkSyncStatus.ok
    link.error_message = None
    link.last_user_sync_at = _utcnow()

    runs_in_db = _count_participant_runs(db, participant.id)
    expected_runs = profile.total_runs
    missing_runs = None
    if expected_runs is not None and runs_in_db < expected_runs:
        missing_runs = expected_runs - runs_in_db

    return {
        "platform_code": platform.code,
        "participant_id": str(participant.id),
        "barcode_id": profile.barcode_id,
        "runs_in_db": runs_in_db,
        "expected_runs_on_profile": expected_runs,
        "missing_runs": missing_runs,
        "runs_imported": runs_imported,
        "volunteering_imported": volunteering_imported,
        "mismatches_found": len(refetch_result.mismatches),
        "protocols_refetched": refetch_result.protocols_refetched,
        "protocols_enqueued": protocol_enqueue.enqueued,
        "protocols_enqueue_checked": protocol_enqueue.checked,
        "parkrun_participant_discovered": parkrun_discovery.found,
        "parkrun_participant_id": parkrun_discovery.participant_id,
        "parkrun_runs_imported": parkrun_discovery.runs_imported,
        "parkrun_volunteering_imported": parkrun_discovery.volunteering_imported,
    }


def run_s95_user_sync(
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
        .filter(PlatformLink.user_id == user_id, Platform.code == "s95")
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
            sync_s95_platform_link(db, link, platform)
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


def sync_s95_link_by_id(db: Session, platform_link_id: UUID) -> dict[str, object]:
    row = (
        db.query(PlatformLink, Platform)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.id == platform_link_id)
        .one_or_none()
    )
    if row is None:
        raise UserSyncError("Platform link not found")
    link, platform = row
    return sync_s95_platform_link(db, link, platform)
