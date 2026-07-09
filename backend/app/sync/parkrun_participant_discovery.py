from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Participant, Platform
from app.parkrun.errors import ParkrunProfileNotFound
from app.s95.parkrun import is_parkrun_eligible_barcode, normalize_parkrun_athlete_id
from app.sync.parkrun_participant_import import import_parkrun_participant_activity

logger = logging.getLogger(__name__)

PLATFORM_CODE = "parkrun"


@dataclass
class ParkrunParticipantDiscoveryResult:
    attempted: bool = False
    found: bool = False
    participant_id: str | None = None
    created: bool = False
    updated: bool = False
    skipped_recent: bool = False
    runs_imported: int = 0
    volunteering_imported: int = 0
    error: str | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_parkrun_platform(db: Session) -> Platform | None:
    return db.query(Platform).filter(Platform.code == PLATFORM_CODE).one_or_none()


def _find_parkrun_participant(db: Session, platform: Platform, athlete_id: str) -> Participant | None:
    return (
        db.query(Participant)
        .filter(
            Participant.platform_id == platform.id,
            Participant.external_user_id == athlete_id,
        )
        .one_or_none()
    )


def _participant_is_fresh(row: Participant, *, min_refetch_days: int) -> bool:
    if row.fetched_at is None:
        return False
    cutoff = _utcnow() - timedelta(days=min_refetch_days)
    return row.fetched_at >= cutoff


def ensure_parkrun_participant_by_athlete_id(
    db: Session,
    athlete_id: str,
    *,
    force_refetch: bool = False,
) -> ParkrunParticipantDiscoveryResult:
    settings = get_settings()
    result = ParkrunParticipantDiscoveryResult()
    cleaned = athlete_id.strip()
    if not cleaned or cleaned.startswith("unknown:"):
        return result

    platform = _get_parkrun_platform(db)
    if platform is None or not platform.is_active:
        return result

    result.attempted = True
    existing = _find_parkrun_participant(db, platform, cleaned)
    if (
        existing is not None
        and not force_refetch
        and _participant_is_fresh(existing, min_refetch_days=settings.parkrun_participant_discovery_min_refetch_days)
    ):
        result.found = True
        result.participant_id = str(existing.id)
        result.skipped_recent = True
        return result

    try:
        imported = import_parkrun_participant_activity(db, platform, cleaned)
    except ParkrunProfileNotFound:
        return result
    except Exception as exc:
        logger.warning("Parkrun participant discovery failed for %s: %s", cleaned, exc)
        result.error = str(exc)
        return result

    result.found = True
    result.participant_id = str(imported.participant.id)
    result.created = imported.created
    result.updated = not imported.created
    result.runs_imported = imported.runs_imported
    result.volunteering_imported = imported.volunteering_imported
    return result


def discover_parkrun_participant_from_barcode(
    db: Session,
    barcode_id: str | None,
    *,
    force_refetch: bool = False,
) -> ParkrunParticipantDiscoveryResult:
    settings = get_settings()
    if not settings.parkrun_participant_discovery_enabled:
        return ParkrunParticipantDiscoveryResult()

    if not is_parkrun_eligible_barcode(barcode_id):
        return ParkrunParticipantDiscoveryResult()

    athlete_id = normalize_parkrun_athlete_id(barcode_id or "")
    if not athlete_id:
        return ParkrunParticipantDiscoveryResult()

    return ensure_parkrun_participant_by_athlete_id(
        db,
        athlete_id,
        force_refetch=force_refetch,
    )


def enqueue_parkrun_discovery_from_barcode(
    db: Session,
    barcode_id: str | None,
) -> ParkrunParticipantDiscoveryResult:
    """Развязанный parkrun-discovery для S95-синка (батч-протоколы и синк профиля).

    НИКОГДА не тянет parkrun инлайн: браузерный fetch parkrun.org.uk не должен
    выполняться на серверном worker-s95 — Chromium там нет, а смешение sync/async
    Playwright вешает процесс воркера намертво. Barcode → parkrun athlete id
    резолвится офлайн; если участник отсутствует или протух — строка ставится в
    profile_fetch_pending, и её подхватывает Mac parkrun-демон. Так parkrun-синк
    полностью отвязан от S95 и не может уронить/подвесить S95-воркер.
    """
    settings = get_settings()
    if not settings.parkrun_participant_discovery_enabled:
        return ParkrunParticipantDiscoveryResult()

    if not is_parkrun_eligible_barcode(barcode_id):
        return ParkrunParticipantDiscoveryResult()

    athlete_id = normalize_parkrun_athlete_id(barcode_id or "")
    if not athlete_id or athlete_id.startswith("unknown:"):
        return ParkrunParticipantDiscoveryResult()

    platform = _get_parkrun_platform(db)
    if platform is None or not platform.is_active:
        return ParkrunParticipantDiscoveryResult()

    result = ParkrunParticipantDiscoveryResult(attempted=True)
    existing = _find_parkrun_participant(db, platform, athlete_id)
    if existing is not None:
        result.found = True
        result.participant_id = str(existing.id)
        if _participant_is_fresh(
            existing,
            min_refetch_days=settings.parkrun_participant_discovery_min_refetch_days,
        ):
            # Свежий профиль — заново тянуть не нужно, очередь не засоряем.
            result.skipped_recent = True
            return result

    from app.services.profile_fetch_pending_service import ensure_parkrun_pending_queue_row

    ensure_parkrun_pending_queue_row(
        db,
        athlete_id,
        note="queued by s95 sync (parkrun decoupled from s95)",
    )
    return result


def enrich_parkrun_protocol_participant(
    db: Session,
    participant: Participant,
    platform: Platform,
) -> ParkrunParticipantDiscoveryResult:
    if platform.code != PLATFORM_CODE:
        return ParkrunParticipantDiscoveryResult()
    if participant.external_user_id.startswith("unknown:"):
        return ParkrunParticipantDiscoveryResult()
    if participant.barcode_id and _participant_is_fresh(
        participant,
        min_refetch_days=get_settings().parkrun_participant_discovery_min_refetch_days,
    ):
        return ParkrunParticipantDiscoveryResult(
            found=True,
            participant_id=str(participant.id),
            skipped_recent=True,
        )
    return ensure_parkrun_participant_by_athlete_id(db, participant.external_user_id)
