from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Participant, Platform, SyncRun, SyncRunStatus
from app.sync import upsert
from app.sync.s95_athletes_sync import sync_s95_athlete

logger = logging.getLogger(__name__)

PLATFORM_CODE = "s95"


@dataclass
class S95AthletesRegistrySyncOptions:
    limit: int | None = None
    mismatch_check_limit: int | None = None


@dataclass
class S95AthletesRegistrySyncResult:
    participants_total: int = 0
    participants_synced: int = 0
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

    platform = upsert.get_platform(db, PLATFORM_CODE)
    result = S95AthletesRegistrySyncResult()
    sync_run = _start_sync_run(db, platform)

    participants = (
        db.query(Participant)
        .filter(Participant.platform_id == platform.id)
        .order_by(Participant.fetched_at.asc().nullsfirst(), Participant.external_user_id.asc())
        .limit(limit)
        .all()
    )
    result.participants_total = len(participants)

    for participant in participants:
        try:
            athlete_result = sync_s95_athlete(
                db,
                participant.external_user_id,
                mismatch_check_limit=mismatch_limit,
            )
            if athlete_result.saved:
                result.participants_synced += 1
            result.runs_imported += athlete_result.runs_imported
            result.protocols_refetched += athlete_result.protocols_refetched
            result.mismatches_found += athlete_result.mismatches_found
            if athlete_result.errors:
                result.errors.extend(athlete_result.errors)
        except Exception as exc:
            logger.exception("S95 athlete registry sync failed for %s", participant.external_user_id)
            result.errors.append(f"{participant.external_user_id}: {exc}")

    _finish_sync_run(
        db,
        sync_run,
        success=not result.errors,
        fetched=result.participants_total,
        upserted=result.participants_synced,
        error="; ".join(result.errors) or None,
    )
    db.commit()
    return result
