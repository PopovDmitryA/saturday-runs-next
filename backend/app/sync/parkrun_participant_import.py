from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Participant, Platform, SyncStatus
from app.parkrun.parsers.athlete import volunteer_summary_to_canonical
from app.platform_adapters.parkrun import parser as parkrun_parser
from app.sync import upsert


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ParkrunParticipantImportResult:
    participant: Participant
    runs_imported: int = 0
    volunteering_imported: int = 0
    created: bool = False
    expected_runs: int | None = None


def apply_parkrun_participant(
    db: Session,
    platform: Platform,
    profile,
    *,
    profile_extra: dict,
) -> tuple[Participant, bool]:
    row = (
        db.query(Participant)
        .filter(
            Participant.platform_id == platform.id,
            Participant.external_user_id == profile.external_user_id,
        )
        .one_or_none()
    )
    created = row is None
    row = upsert.upsert_participant(
        db,
        platform,
        external_user_id=profile.external_user_id,
        display_name=profile.display_name,
        profile_url=profile.profile_url,
        age_category=profile_extra.get("age_category"),
    )
    row.barcode_id = profile.barcode_id
    row.age_category = profile_extra.get("age_category")
    row.profile_extra = profile_extra
    row.source_url = profile.source_url or profile.profile_url
    row.parser_version = upsert.PARSER_VERSION
    row.fetched_at = _utcnow()
    row.sync_status = SyncStatus.ok
    row.error_message = None
    db.flush()
    return row, created


def import_parkrun_participant_activity(
    db: Session,
    platform: Platform,
    athlete_id: str,
    *,
    user_id: UUID | None = None,
) -> ParkrunParticipantImportResult:
    from app.models import ProfileFetchPendingOperation
    from app.parkrun.errors import ParkrunBanDetected
    from app.services.profile_fetch_pending_service import enqueue_profile_fetch_pending

    try:
        profile, runs, _volunteering, profile_extra = parkrun_parser.fetch_athlete_by_id(athlete_id)
    except ParkrunBanDetected as exc:
        enqueue_profile_fetch_pending(
            db,
            platform_code="parkrun",
            profile_input=athlete_id,
            user_id=user_id,
            operation=ProfileFetchPendingOperation.activity_import,
            exc=exc,
        )
        db.commit()
        raise
    volunteering = volunteer_summary_to_canonical(
        profile_extra.get("volunteer_summary") or [],
        athlete_id=profile.external_user_id,
        display_name=profile.display_name,
    )
    participant, created = apply_parkrun_participant(db, platform, profile, profile_extra=profile_extra)
    runs_imported = upsert.import_profile_run_results(db, platform, runs)
    volunteering_imported = upsert.import_profile_volunteer_results(
        db,
        platform,
        participant.id,
        volunteering,
    )
    from app.services.parkrun_pr_service import recalculate_parkrun_personal_records

    recalculate_parkrun_personal_records(db, participant_id=participant.id)
    return ParkrunParticipantImportResult(
        participant=participant,
        runs_imported=runs_imported,
        volunteering_imported=volunteering_imported,
        created=created,
        expected_runs=profile.total_runs,
    )
