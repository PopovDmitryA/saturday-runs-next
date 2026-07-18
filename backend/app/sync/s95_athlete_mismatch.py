from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, RunResult
from app.platform_adapters.canonical import CanonicalRunResult
from app.sync import upsert

DEFAULT_MISMATCH_CHECK_RUNS = 10


@dataclass(frozen=True)
class ProfileRunMismatch:
    external_event_key: str
    location_slug: str
    event_date: date
    profile_position: int | None
    profile_finish_time_sec: int | None
    db_position: int | None
    db_finish_time_sec: int | None


def _find_db_run_result(
    db: Session,
    platform: Platform,
    participant_id,
    slug: str,
    event_date: date,
) -> RunResult | None:
    location = (
        db.query(Location)
        .filter(
            Location.platform_id == platform.id,
            Location.external_key == slug,
        )
        .one_or_none()
    )
    if location is None:
        return None
    event = upsert._find_event_by_location_date(db, platform, location.id, event_date)
    if event is None:
        event = (
            db.query(Event)
            .filter(
                Event.platform_id == platform.id,
                Event.external_event_key == f"{slug}:{event_date.isoformat()}",
            )
            .one_or_none()
        )
    if event is None:
        return None
    return (
        db.query(RunResult)
        .filter(
            RunResult.participant_id == participant_id,
            RunResult.event_id == event.id,
        )
        .one_or_none()
    )


def detect_profile_run_mismatches(
    db: Session,
    platform: Platform,
    participant: Participant,
    profile_runs: list[CanonicalRunResult],
    *,
    limit: int | None = DEFAULT_MISMATCH_CHECK_RUNS,
) -> list[ProfileRunMismatch]:
    recent = sorted(profile_runs, key=lambda item: item.event_date, reverse=True)
    if limit is not None:
        recent = recent[:limit]
    mismatches: list[ProfileRunMismatch] = []
    for profile_run in recent:
        slug = upsert._normalize_location_slug(
            profile_run.location_external_key,
            profile_run.location_name,
        )
        if slug == "unknown":
            continue
        db_run = _find_db_run_result(db, platform, participant.id, slug, profile_run.event_date)
        if db_run is None:
            continue
        position_mismatch = (
            profile_run.position is not None
            and db_run.position is not None
            and profile_run.position != db_run.position
        )
        time_mismatch = (
            profile_run.finish_time_sec is not None
            and db_run.finish_time_sec is not None
            and profile_run.finish_time_sec != db_run.finish_time_sec
        )
        if not position_mismatch and not time_mismatch:
            continue
        mismatches.append(
            ProfileRunMismatch(
                external_event_key=f"{slug}:{profile_run.event_date.isoformat()}",
                location_slug=slug,
                event_date=profile_run.event_date,
                profile_position=profile_run.position,
                profile_finish_time_sec=profile_run.finish_time_sec,
                db_position=db_run.position,
                db_finish_time_sec=db_run.finish_time_sec,
            )
        )
    return mismatches
