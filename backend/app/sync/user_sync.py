from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import (
    Participant,
    Platform,
    PlatformLink,
    PlatformLinkSyncStatus,
    SyncJob,
    SyncJobStatus,
    SyncJobTrigger,
    SyncStatus,
    User,
)
from app.platform_adapters.canonical import CanonicalParticipant
from app.platform_adapters.five_verst.parser import (
    fetch_userstats_html,
    parse_userstats_html,
    parse_userstats_runs_html,
    parse_userstats_volunteering_html,
)
from app.platform_adapters.five_verst.url import normalize_profile_url, userstats_url
from app.platform_adapters.registry import ensure_adapters_registered, get_adapter
from app.services.dashboard_service import recompute_dashboard_cache
from app.sync import upsert as sync_upsert


class UserSyncError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _apply_participant_profile(db: Session, platform: Platform, profile: CanonicalParticipant) -> Participant:
    row = sync_upsert.upsert_participant(
        db,
        platform,
        external_user_id=profile.external_user_id,
        display_name=profile.display_name,
        profile_url=profile.profile_url,
    )
    row.club_name = profile.club_name
    row.barcode_id = profile.barcode_id
    row.planning_location = profile.planning_location
    row.source_url = profile.source_url or profile.profile_url
    row.parser_version = sync_upsert.PARSER_VERSION
    row.fetched_at = _utcnow()
    row.sync_status = SyncStatus.ok
    row.error_message = None
    db.flush()
    return row


def _count_participant_runs(db: Session, participant_id: UUID) -> int:
    from app.models import Event, RunResult

    return (
        db.query(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .filter(
            RunResult.participant_id == participant_id,
            Event.is_test_event.is_(False),
        )
        .count()
    )


def sync_platform_link(
    db: Session,
    link: PlatformLink,
    platform: Platform,
) -> dict[str, object]:
    if platform.code == "s95":
        from app.sync.s95_user_sync import sync_s95_platform_link

        return sync_s95_platform_link(db, link, platform)

    if platform.code != "five_verst":
        ensure_adapters_registered()
        adapter = get_adapter(platform.code)
        if not adapter.capabilities.fetch_user_profile:
            raise UserSyncError(f"User sync not supported for platform {platform.code}")
        profile = adapter.fetch_user_profile(link.external_user_id)
        participant = _apply_participant_profile(db, platform, profile)
        link.participant_id = participant.id
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
            "runs_in_db": runs_in_db,
            "expected_runs_on_profile": expected_runs,
            "missing_runs": missing_runs,
            "runs_imported": 0,
            "volunteering_imported": 0,
        }

    profile_url = userstats_url(link.external_user_id)
    html = fetch_userstats_html(profile_url)
    parsed_url = normalize_profile_url(profile_url)
    profile = parse_userstats_html(html, parsed_url)
    participant = _apply_participant_profile(db, platform, profile)
    link.participant_id = participant.id

    runs = parse_userstats_runs_html(html, profile.external_user_id, profile.display_name)
    volunteering = parse_userstats_volunteering_html(html, profile.external_user_id, profile.display_name)
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
        "runs_in_db": runs_in_db,
        "expected_runs_on_profile": expected_runs,
        "missing_runs": missing_runs,
        "runs_imported": runs_imported,
        "volunteering_imported": volunteering_imported,
        "protocols_enqueued": protocol_enqueue.enqueued,
        "protocols_enqueue_checked": protocol_enqueue.checked,
    }


def run_user_sync(
    db: Session,
    user_id: UUID,
    trigger: SyncJobTrigger,
    *,
    platform_link_id: UUID | None = None,
    existing_job: SyncJob | None = None,
) -> SyncJob:
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if user is None:
        raise UserSyncError("User not found")

    job = existing_job
    if job is None:
        from app.services.dashboard_service import create_sync_job

        job = create_sync_job(db, user_id, trigger, platform_link_id=platform_link_id)

    job.status = SyncJobStatus.running
    job.started_at = _utcnow()
    db.commit()

    link_query = db.query(PlatformLink, Platform).join(Platform, PlatformLink.platform_id == Platform.id).filter(
        PlatformLink.user_id == user_id
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

    results: list[dict[str, object]] = []
    errors: list[str] = []

    for link, platform in links:
        if not platform.is_active:
            continue
        if platform.code in ("s95", "parkrun"):
            continue
        platform_code = platform.code
        link.sync_status = PlatformLinkSyncStatus.syncing
        db.commit()
        try:
            results.append(sync_platform_link(db, link, platform))
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
