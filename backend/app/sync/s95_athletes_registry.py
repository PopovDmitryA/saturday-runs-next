from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Participant, Platform, SyncRun, SyncRunStatus
from app.sync import upsert
from app.s95.fetch.priority import S95YieldForUserSync
from app.sync.s95_athletes_sync import sync_s95_athlete
from app.sync.s95_participant_sync import (
    BARCODE_LOOKUP_CHECKED_AT_KEY,
    PROFILE_CHECKED_AT_KEY,
    profile_checked_at,
)

logger = logging.getLogger(__name__)

PLATFORM_CODE = "s95"


@dataclass
class S95AthletesRegistrySyncOptions:
    limit: int | None = None
    mismatch_check_limit: int | None = None
    recheck_interval_days: int | None = None


@dataclass
class S95AthletesRegistrySyncResult:
    participants_total: int = 0
    participants_synced: int = 0
    participants_skipped: int = 0
    runs_imported: int = 0
    protocols_refetched: int = 0
    mismatches_found: int = 0
    errors: list[str] = field(default_factory=list)


def _start_sync_run(db: Session, platform: Platform) -> SyncRun:
    run = SyncRun(
        platform_id=platform.id,
        sync_type="s95:athletes_registry",
        status=SyncRunStatus.running,
        parser_version=upsert.PARSER_VERSION,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    return run


def _finish_sync_run(
    db: Session,
    run: SyncRun,
    *,
    success: bool,
    fetched: int,
    upserted: int,
    error: str | None = None,
) -> None:
    run.status = SyncRunStatus.success if success else SyncRunStatus.failed
    run.finished_at = datetime.now(timezone.utc)
    run.records_fetched = fetched
    run.records_upserted = upserted
    run.error_message = error
    db.flush()


def participant_due_for_profile_recheck(row: Participant, *, cutoff: datetime) -> bool:
    last_check = profile_checked_at(row.profile_extra)
    return last_check is None or last_check < cutoff


def participants_for_profile_recheck(
    db: Session,
    platform: Platform,
    *,
    limit: int,
    recheck_interval_days: int,
) -> list[Participant]:
    """S95 athlete pages due for profile recheck (oldest check first).

    Like protocol reconcile: even previously unavailable profiles are rechecked
    after the interval — the athlete may appear or gain a barcode later.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=recheck_interval_days)
    cutoff_iso = cutoff.isoformat()
    checked_at = Participant.profile_extra[PROFILE_CHECKED_AT_KEY].astext
    legacy_checked_at = Participant.profile_extra[BARCODE_LOOKUP_CHECKED_AT_KEY].astext
    return (
        db.query(Participant)
        .filter(
            Participant.platform_id == platform.id,
            Participant.external_user_id.op("~")(r"^\d+$"),
            or_(
                and_(checked_at.is_(None), legacy_checked_at.is_(None)),
                checked_at < cutoff_iso,
                and_(checked_at.is_(None), legacy_checked_at < cutoff_iso),
            ),
        )
        .order_by(checked_at.asc().nullsfirst(), legacy_checked_at.asc().nullsfirst())
        .limit(limit)
        .all()
    )


def sync_s95_athletes_registry(
    db: Session,
    options: S95AthletesRegistrySyncOptions | None = None,
) -> S95AthletesRegistrySyncResult:
    settings = get_settings()
    options = options or S95AthletesRegistrySyncOptions()
    limit = options.limit if options.limit is not None else settings.s95_athletes_registry_batch_limit
    mismatch_limit = (
        options.mismatch_check_limit
        if options.mismatch_check_limit is not None
        else settings.s95_athlete_mismatch_check_runs
    )
    recheck_days = (
        options.recheck_interval_days
        if options.recheck_interval_days is not None
        else settings.s95_athlete_profile_recheck_interval_days
    )

    platform = upsert.get_platform(db, PLATFORM_CODE)
    result = S95AthletesRegistrySyncResult()
    sync_run = _start_sync_run(db, platform)

    participants = participants_for_profile_recheck(
        db,
        platform,
        limit=limit,
        recheck_interval_days=recheck_days,
    )
    result.participants_total = len(participants)

    yielded_for_user = False
    for participant in participants:
        try:
            athlete_result = sync_s95_athlete(
                db,
                participant.external_user_id,
                mismatch_check_limit=mismatch_limit,
            )
            if athlete_result.saved:
                result.participants_synced += 1
            elif athlete_result.skipped:
                result.participants_skipped += 1
            result.runs_imported += athlete_result.runs_imported
            result.protocols_refetched += athlete_result.protocols_refetched
            result.mismatches_found += athlete_result.mismatches_found
            if athlete_result.errors and not athlete_result.skipped:
                result.errors.extend(athlete_result.errors)
        except S95YieldForUserSync:
            yielded_for_user = True
            break
        except Exception as exc:
            logger.exception("S95 athlete registry sync failed for %s", participant.external_user_id)
            result.errors.append(f"{participant.external_user_id}: {exc}")

    _finish_sync_run(
        db,
        sync_run,
        success=not result.errors and not yielded_for_user,
        fetched=result.participants_total,
        upserted=result.participants_synced + result.participants_skipped,
        error="; ".join(result.errors) or None,
    )
    db.commit()
    if yielded_for_user:
        raise S95YieldForUserSync()
    return result
