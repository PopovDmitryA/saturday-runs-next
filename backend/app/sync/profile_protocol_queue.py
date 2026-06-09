"""Enqueue full event protocol fetch when profile sync finds incomplete events."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.models import EventSummary, Location, Participant, Platform, ProtocolSyncState
from app.platform_adapters.canonical import (
    CanonicalEventSummary,
    CanonicalRunResult,
    CanonicalVolunteerResult,
)
from app.services.batch_queue_guard import batch_queue_has_capacity
from app.services.celery_queue_inspector import FIVE_VERST_BATCH_QUEUE, S95_BATCH_QUEUE
from app.services.protocol_queue_dedup import (
    protocol_fetch_pending_in_broker,
    protocol_fetch_task_id,
)
from app.sync import upsert

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_PROTOCOL_ENQUEUE_LIMIT = 30


@dataclass(frozen=True)
class ProfileEventRef:
    platform_code: str
    location_slug: str
    location_name: str
    event_date: date
    event_number: int | None = None


@dataclass
class ProfileProtocolEnqueueResult:
    checked: int = 0
    missing: int = 0
    enqueued: int = 0
    skipped_loaded: int = 0
    skipped_duplicate: int = 0
    event_keys: list[str] = field(default_factory=list)


def is_protocol_fully_loaded(
    db: Session,
    platform: Platform,
    *,
    location_slug: str,
    event_date: date,
) -> bool:
    """True when the full event protocol was fetched at least once (not just userstats rows)."""
    location = (
        db.query(Location)
        .filter(
            Location.platform_id == platform.id,
            Location.external_key == location_slug,
        )
        .one_or_none()
    )
    if location is None:
        return False

    event = upsert._find_event_by_location_date(db, platform, location.id, event_date)
    if event is None:
        summary = (
            db.query(EventSummary)
            .filter(
                EventSummary.platform_id == platform.id,
                EventSummary.location_id == location.id,
                EventSummary.event_date == event_date,
            )
            .one_or_none()
        )
        if summary is None or summary.event_id is None:
            return False
        event_id = summary.event_id
    else:
        event_id = event.id

    state = (
        db.query(ProtocolSyncState)
        .filter(ProtocolSyncState.event_id == event_id)
        .one_or_none()
    )
    return state is not None and state.last_protocol_fetched_at is not None


def collect_profile_event_refs(
    runs: list[CanonicalRunResult],
    volunteering: list[CanonicalVolunteerResult],
    *,
    platform_code: str,
    limit: int | None = DEFAULT_PROFILE_PROTOCOL_ENQUEUE_LIMIT,
) -> list[ProfileEventRef]:
    seen: set[tuple[str, date]] = set()
    refs: list[ProfileEventRef] = []

    def _add(slug: str, name: str, event_date: date, event_number: int | None) -> None:
        if slug == "unknown":
            return
        key = (slug, event_date)
        if key in seen:
            return
        seen.add(key)
        refs.append(
            ProfileEventRef(
                platform_code=platform_code,
                location_slug=slug,
                location_name=name or slug,
                event_date=event_date,
                event_number=event_number,
            )
        )

    for item in sorted(runs, key=lambda row: row.event_date, reverse=True):
        slug = upsert._normalize_location_slug(item.location_external_key, item.location_name)
        _add(slug, item.location_name or slug, item.event_date, item.event_number)
        if limit is not None and len(refs) >= limit:
            return refs

    for item in sorted(volunteering, key=lambda row: row.event_date, reverse=True):
        slug = upsert._normalize_location_slug(item.location_external_key, item.location_name)
        _add(slug, item.location_name or slug, item.event_date, None)
        if limit is not None and len(refs) >= limit:
            break

    return refs


def enqueue_missing_protocols_for_profile(
    db: Session,
    platform: Platform,
    runs: list[CanonicalRunResult],
    volunteering: list[CanonicalVolunteerResult],
    *,
    limit: int | None = DEFAULT_PROFILE_PROTOCOL_ENQUEUE_LIMIT,
) -> ProfileProtocolEnqueueResult:
    result = ProfileProtocolEnqueueResult()
    refs = collect_profile_event_refs(
        runs,
        volunteering,
        platform_code=platform.code,
        limit=limit,
    )
    result.checked = len(refs)

    for ref in refs:
        if is_protocol_fully_loaded(
            db,
            platform,
            location_slug=ref.location_slug,
            event_date=ref.event_date,
        ):
            result.skipped_loaded += 1
            continue
        result.missing += 1
        if _dispatch_protocol_fetch(ref, result=result):
            result.enqueued += 1
            result.event_keys.append(f"{ref.location_slug}:{ref.event_date.isoformat()}")

    if result.enqueued:
        logger.info(
            "profile protocol enqueue %s: checked=%d missing=%d enqueued=%d",
            platform.code,
            result.checked,
            result.missing,
            result.enqueued,
        )
    return result


def _dispatch_protocol_fetch(
    ref: ProfileEventRef,
    *,
    result: ProfileProtocolEnqueueResult | None = None,
    force: bool = False,
) -> bool:
    if protocol_fetch_pending_in_broker(
        ref.platform_code,
        ref.location_slug,
        ref.event_date,
    ):
        if result is not None:
            result.skipped_duplicate += 1
        logger.debug(
            "skip duplicate protocol fetch %s %s %s",
            ref.platform_code,
            ref.location_slug,
            ref.event_date.isoformat(),
        )
        return False

    task_id = protocol_fetch_task_id(ref.platform_code, ref.location_slug, ref.event_date)

    if ref.platform_code == "five_verst":
        if not batch_queue_has_capacity(FIVE_VERST_BATCH_QUEUE):
            logger.info(
                "skip profile protocol enqueue for %s %s: five_verst batch queue full",
                ref.location_slug,
                ref.event_date.isoformat(),
            )
            return False
        from app.workers.tasks.five_verst_sync import fetch_protocol_from_profile_task

        fetch_protocol_from_profile_task.apply_async(
            kwargs={
                "location_slug": ref.location_slug,
                "event_date_iso": ref.event_date.isoformat(),
                "event_number": ref.event_number,
                "location_name": ref.location_name,
            },
            queue=FIVE_VERST_BATCH_QUEUE,
            task_id=task_id,
        )
        return True
    if ref.platform_code == "s95":
        if not batch_queue_has_capacity(S95_BATCH_QUEUE):
            logger.info(
                "skip profile protocol enqueue for %s %s: s95 batch queue full",
                ref.location_slug,
                ref.event_date.isoformat(),
            )
            return False
        from app.workers.tasks.s95_sync import fetch_protocol_from_profile_task

        fetch_protocol_from_profile_task.apply_async(
            kwargs={
                "location_slug": ref.location_slug,
                "event_date_iso": ref.event_date.isoformat(),
                "location_name": ref.location_name,
                "force": force,
            },
            queue=S95_BATCH_QUEUE,
            task_id=task_id,
        )
        return True
    return False


def enqueue_mismatched_protocols_for_profile(
    db: Session,
    platform: Platform,
    participant: Participant,
    profile_runs: list[CanonicalRunResult],
    profile_volunteering: list[CanonicalVolunteerResult],
    *,
    limit: int | None = None,
) -> tuple[ProfileProtocolEnqueueResult, int]:
    """Enqueue protocol refetch for mismatched runs and recent volunteering (no inline fetch)."""
    from app.sync.s95_athlete_mismatch import detect_profile_run_mismatches

    if platform.code != "s95":
        return ProfileProtocolEnqueueResult(), 0

    mismatches = detect_profile_run_mismatches(
        db,
        platform,
        participant,
        profile_runs,
        limit=limit,
    )
    result = ProfileProtocolEnqueueResult()
    result.checked = len(mismatches)

    seen: set[tuple[str, date]] = set()
    for mismatch in mismatches:
        key = (mismatch.location_slug, mismatch.event_date)
        if key in seen:
            continue
        seen.add(key)
        profile_run = next(
            (
                item
                for item in profile_runs
                if upsert._normalize_location_slug(item.location_external_key, item.location_name)
                == mismatch.location_slug
                and item.event_date == mismatch.event_date
            ),
            None,
        )
        ref = ProfileEventRef(
            platform_code=platform.code,
            location_slug=mismatch.location_slug,
            location_name=(profile_run.location_name if profile_run else mismatch.location_slug),
            event_date=mismatch.event_date,
            event_number=profile_run.event_number if profile_run else None,
        )
        if _dispatch_protocol_fetch(ref, result=result, force=True):
            result.enqueued += 1
            result.event_keys.append(f"{ref.location_slug}:{ref.event_date.isoformat()}")

    recent_vol = sorted(profile_volunteering, key=lambda item: item.event_date, reverse=True)
    if limit is not None:
        recent_vol = recent_vol[:limit]
    for vol in recent_vol:
        slug = upsert._normalize_location_slug(vol.location_external_key, vol.location_name)
        if slug == "unknown":
            continue
        key = (slug, vol.event_date)
        if key in seen:
            continue
        seen.add(key)
        ref = ProfileEventRef(
            platform_code=platform.code,
            location_slug=slug,
            location_name=vol.location_name or slug,
            event_date=vol.event_date,
        )
        if _dispatch_protocol_fetch(ref, result=result, force=True):
            result.enqueued += 1
            result.event_keys.append(f"{ref.location_slug}:{ref.event_date.isoformat()}")

    if result.enqueued:
        logger.info(
            "profile mismatch protocol enqueue %s participant=%s: mismatches=%d enqueued=%d",
            platform.code,
            participant.id,
            len(mismatches),
            result.enqueued,
        )
    return result, len(mismatches)


def _summary_row_to_canonical(summary_row: EventSummary, location: Location) -> CanonicalEventSummary:
    return CanonicalEventSummary(
        external_event_key=summary_row.external_event_key,
        event_date=summary_row.event_date,
        event_number=summary_row.event_number,
        location_external_key=location.external_key,
        location_name=location.name,
        finishers_count=summary_row.finishers_count,
        volunteers_count=summary_row.volunteers_count,
        avg_time_sec=summary_row.avg_time_sec,
        avg_time_display=summary_row.avg_time_display,
        best_female_time_sec=summary_row.best_female_time_sec,
        best_female_time_display=summary_row.best_female_time_display,
        best_male_time_sec=summary_row.best_male_time_sec,
        best_male_time_display=summary_row.best_male_time_display,
        is_test_event=summary_row.is_test_event,
        source_url=summary_row.source_url or "",
        summary_hash=summary_row.summary_hash,
        summary_extra=summary_row.summary_extra or {},
    )


def fetch_five_verst_protocol_for_profile(
    db: Session,
    *,
    location_slug: str,
    event_date: date,
    event_number: int | None,
    location_name: str,
) -> None:
    from app.platform_adapters.five_verst import bulk_parser
    from app.sync.five_verst_protocol import fetch_and_upsert_event_protocol
    from app.sync.iteration_commit import commit_step

    platform = upsert.get_platform(db, "five_verst")
    if is_protocol_fully_loaded(db, platform, location_slug=location_slug, event_date=event_date):
        return

    location = (
        db.query(Location)
        .filter(
            Location.platform_id == platform.id,
            Location.external_key == location_slug,
        )
        .one_or_none()
    )
    if location is None:
        location_data, location_html = bulk_parser.fetch_location(location_slug)
        location, _ = upsert.upsert_location(
            db,
            platform,
            location_data,
            source_hash=bulk_parser.source_hash(location_html),
        )

    summary_row = (
        db.query(EventSummary)
        .filter(
            EventSummary.platform_id == platform.id,
            EventSummary.location_id == location.id,
            EventSummary.event_date == event_date,
        )
        .one_or_none()
    )
    if summary_row is None:
        summaries, _ = bulk_parser.fetch_event_summaries(location_slug, location.name)
        for summary in summaries:
            if summary.event_date == event_date:
                summary_row, _ = upsert.upsert_event_summary(db, platform, location, summary)
                break

    if summary_row is None:
        external_event_key = f"{location_slug}:{event_number or 0}:{event_date.isoformat()}"
        stub = CanonicalEventSummary(
            external_event_key=external_event_key,
            event_date=event_date,
            event_number=event_number,
            location_external_key=location_slug,
            location_name=location_name or location.name,
            source_url=f"https://5verst.ru/{location_slug}/results/{event_date.strftime('%d.%m.%Y')}/",
            summary_hash="profile_stub",
        )
        summary_row, _ = upsert.upsert_event_summary(db, platform, location, stub)

    summary = _summary_row_to_canonical(summary_row, location)
    fetch_and_upsert_event_protocol(db, platform, location, summary, summary_row)
    commit_step(db)


def fetch_s95_protocol_for_profile(
    db: Session,
    *,
    location_slug: str,
    event_date: date,
    location_name: str,
    force: bool = False,
) -> None:
    from app.sync.iteration_commit import commit_step
    from app.sync.s95_protocol import fetch_and_upsert_activity_protocol
    from app.sync.s95_protocol_lookup import resolve_s95_protocol

    platform = upsert.get_platform(db, "s95")
    if not force and is_protocol_fully_loaded(
        db, platform, location_slug=location_slug, event_date=event_date
    ):
        return

    resolved = resolve_s95_protocol(
        db,
        platform,
        location_slug=location_slug,
        location_name=location_name,
        event_date=event_date,
    )
    if resolved is None:
        raise ValueError(f"S95 protocol not found: {location_slug} {event_date}")

    fetch_and_upsert_activity_protocol(
        db,
        platform,
        resolved.location,
        resolved.summary,
        resolved.summary_row,
        protocol_url=resolved.protocol_url,
    )
    commit_step(db)
