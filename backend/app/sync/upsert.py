from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.activity_url import prefer_event_source_url
from app.models import (
    Event,
    EventSummary,
    Location,
    Participant,
    Platform,
    RunResult,
    SyncStatus,
    VolunteerResult,
)
from app.pace import resolve_run_pace
from app.platform_adapters.canonical import (
    CanonicalEventSummary,
    CanonicalLocation,
    CanonicalRunResult,
    CanonicalVolunteerResult,
)

PARSER_VERSION = "0.3.2"
logger = logging.getLogger(__name__)

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _resolve_region(location: CanonicalLocation) -> str | None:
    if location.region:
        return location.region
    if location.latitude is None or location.longitude is None:
        return None
    try:
        from app.geo.reverse_geocode import lookup_region

        return lookup_region(location.latitude, location.longitude)
    except Exception:
        return None


def get_platform(db: Session, platform_code: str) -> Platform:
    platform = db.query(Platform).filter(Platform.code == platform_code).one_or_none()
    if platform is None:
        raise ValueError(f"Platform not found: {platform_code}")
    return platform


def upsert_location(
    db: Session,
    platform: Platform,
    location: CanonicalLocation,
    *,
    source_hash: str | None = None,
) -> tuple[Location, bool]:
    row = (
        db.query(Location)
        .filter(
            Location.platform_id == platform.id,
            Location.external_key == location.external_key,
        )
        .one_or_none()
    )
    now = datetime.now(timezone.utc)
    changed = False
    if location.latitude is not None and location.longitude is not None and location.region is None:
        logger.info("geocode region for %s", location.external_key)
    region = _resolve_region(location)

    if row is None:
        row = Location(
            platform_id=platform.id,
            external_key=location.external_key,
            name=location.name,
            country=location.country,
            city=location.city,
            region=region,
            latitude=location.latitude,
            longitude=location.longitude,
            map_url=location.map_url,
            source_url=location.source_url,
            parser_version=PARSER_VERSION,
            source_hash=source_hash,
            fetched_at=now,
            sync_status=SyncStatus.ok,
        )
        db.add(row)
        db.flush()
        return row, True

    if row.source_hash == source_hash and source_hash is not None:
        if row.region is None and region is not None:
            row.region = region
            db.flush()
        return row, False

    row.name = location.name
    row.country = location.country
    row.city = location.city or row.city
    row.region = region or row.region
    row.latitude = location.latitude or row.latitude
    row.longitude = location.longitude or row.longitude
    if location.map_url:
        row.map_url = location.map_url
    row.source_url = location.source_url
    row.parser_version = PARSER_VERSION
    row.source_hash = source_hash
    row.fetched_at = now
    row.sync_status = SyncStatus.ok
    row.error_message = None
    changed = True
    logger.info("DB flush location %s", location.external_key)
    db.flush()
    return row, changed


def upsert_event_summary(
    db: Session,
    platform: Platform,
    location: Location,
    summary: CanonicalEventSummary,
) -> tuple[EventSummary, bool]:
    row = (
        db.query(EventSummary)
        .filter(
            EventSummary.platform_id == platform.id,
            EventSummary.external_event_key == summary.external_event_key,
        )
        .one_or_none()
    )
    now = datetime.now(timezone.utc)
    unchanged = row is not None and row.summary_hash == summary.summary_hash

    if row is None:
        row = EventSummary(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=summary.external_event_key,
            event_date=summary.event_date,
            event_number=summary.event_number,
            is_test_event=summary.is_test_event,
            finishers_count=summary.finishers_count,
            volunteers_count=summary.volunteers_count,
            avg_time_sec=summary.avg_time_sec,
            avg_time_display=summary.avg_time_display,
            best_female_time_sec=summary.best_female_time_sec,
            best_female_time_display=summary.best_female_time_display,
            best_male_time_sec=summary.best_male_time_sec,
            best_male_time_display=summary.best_male_time_display,
            summary_hash=summary.summary_hash,
            summary_extra=summary.summary_extra or {},
            source_url=summary.source_url,
            parser_version=PARSER_VERSION,
            source_hash=summary.summary_hash,
            fetched_at=now,
            sync_status=SyncStatus.ok,
        )
        db.add(row)
        db.flush()
        return row, True

    if unchanged:
        row.sync_status = SyncStatus.unchanged
        db.flush()
        return row, False

    row.location_id = location.id
    row.event_date = summary.event_date
    row.event_number = summary.event_number
    row.is_test_event = summary.is_test_event
    row.finishers_count = summary.finishers_count
    row.volunteers_count = summary.volunteers_count
    row.avg_time_sec = summary.avg_time_sec
    row.avg_time_display = summary.avg_time_display
    row.best_female_time_sec = summary.best_female_time_sec
    row.best_female_time_display = summary.best_female_time_display
    row.best_male_time_sec = summary.best_male_time_sec
    row.best_male_time_display = summary.best_male_time_display
    row.summary_hash = summary.summary_hash
    row.summary_extra = summary.summary_extra or {}
    row.source_url = summary.source_url
    row.parser_version = PARSER_VERSION
    row.source_hash = summary.summary_hash
    row.fetched_at = now
    row.sync_status = SyncStatus.ok
    row.error_message = None
    db.flush()
    return row, True


def upsert_event_for_summary(
    db: Session,
    platform: Platform,
    location: Location,
    summary: CanonicalEventSummary,
    summary_row: EventSummary,
) -> Event:
    row = _find_existing_event(
        db,
        platform,
        location,
        event_date=summary.event_date,
        external_event_key=summary.external_event_key,
        location_slug=summary.location_external_key,
        location_name=summary.location_name,
    )
    now = datetime.now(timezone.utc)
    title = f"{summary.location_name} #{summary.event_number}"
    if summary.is_test_event:
        title = f"{summary.location_name} (тестовый)"

    if row is None:
        row = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=summary.external_event_key,
            event_date=summary.event_date,
            event_number=summary.event_number,
            is_test_event=summary.is_test_event,
            title=title,
            finishers_count=summary.finishers_count,
            runners_count=summary.finishers_count,
            source_url=summary.source_url,
            parser_version=PARSER_VERSION,
            source_hash=summary.summary_hash,
            fetched_at=now,
            sync_status=SyncStatus.ok,
        )
        db.add(row)
        try:
            with db.begin_nested():
                db.flush()
        except IntegrityError:
            db.expunge(row)
            row = _find_existing_event(
                db,
                platform,
                location,
                event_date=summary.event_date,
                external_event_key=summary.external_event_key,
                location_slug=summary.location_external_key,
                location_name=summary.location_name,
            )
            if row is None:
                raise

    _assign_external_event_key(db, platform, row, summary.external_event_key)
    row.location_id = location.id
    row.event_date = summary.event_date
    row.event_number = summary.event_number
    row.is_test_event = summary.is_test_event
    row.title = title
    row.finishers_count = summary.finishers_count
    row.runners_count = summary.finishers_count
    row.source_url = summary.source_url
    row.parser_version = PARSER_VERSION
    row.source_hash = summary.summary_hash
    row.fetched_at = now
    row.sync_status = SyncStatus.ok
    row.error_message = None

    try:
        db.flush()
    except IntegrityError:
        db.expire(row)
        row = _find_existing_event(
            db,
            platform,
            location,
            event_date=summary.event_date,
            external_event_key=summary.external_event_key,
            location_slug=summary.location_external_key,
            location_name=summary.location_name,
        )
        if row is None:
            raise
        _assign_external_event_key(db, platform, row, summary.external_event_key)
        db.flush()
    summary_row.event_id = row.id
    db.flush()
    return row


def upsert_participant(
    db: Session,
    platform: Platform,
    *,
    external_user_id: str,
    display_name: str,
    profile_url: str | None = None,
    club_name: str | None = None,
    age_category: str | None = None,
) -> Participant:
    row = (
        db.query(Participant)
        .filter(
            Participant.platform_id == platform.id,
            Participant.external_user_id == external_user_id,
        )
        .one_or_none()
    )
    now = datetime.now(timezone.utc)
    default_profile_url = (
        None if external_user_id.startswith("unknown:") else f"https://5verst.ru/userstats/{external_user_id}/"
    )
    if row is None:
        row = Participant(
            platform_id=platform.id,
            external_user_id=external_user_id,
            display_name=display_name,
            profile_url=profile_url or default_profile_url,
            club_name=club_name,
            age_category=age_category,
            source_url=profile_url,
            parser_version=PARSER_VERSION,
            fetched_at=now,
            sync_status=SyncStatus.ok,
        )
        db.add(row)
    else:
        row.display_name = display_name
        if profile_url:
            row.profile_url = profile_url
        if club_name:
            row.club_name = club_name
        if age_category:
            row.age_category = age_category
        row.fetched_at = now
        row.sync_status = SyncStatus.ok
    db.flush()
    return row


def upsert_run_results(
    db: Session,
    event: Event,
    platform: Platform,
    results: list[CanonicalRunResult],
    *,
    from_profile: bool = False,
    recalculate_pr: bool = True,
) -> int:
    upserted = 0
    touched_participant_ids: set = set()
    for item in results:
        participant = upsert_participant(
            db,
            platform,
            external_user_id=item.external_user_id,
            display_name=item.participant_name,
            club_name=None if from_profile else item.club_name,
            age_category=None if from_profile else item.age_category,
        )
        touched_participant_ids.add(participant.id)
        row = (
            db.query(RunResult)
            .filter(
                RunResult.event_id == event.id,
                RunResult.external_result_key == item.external_result_key,
            )
            .one_or_none()
        )
        if row is None:
            row = (
                db.query(RunResult)
                .filter(
                    RunResult.event_id == event.id,
                    RunResult.participant_id == participant.id,
                )
                .one_or_none()
            )
        pace_sec_per_km, pace_display = resolve_run_pace(
            item.pace_sec_per_km,
            item.pace_display,
            item.finish_time_sec,
        )
        now = datetime.now(timezone.utc)
        if row is None:
            row = RunResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=item.external_result_key,
                position=item.position,
                finish_time_sec=item.finish_time_sec,
                finish_time_display=item.finish_time_display,
                pace_sec_per_km=pace_sec_per_km,
                pace_display=pace_display,
                age_category=item.age_category,
                status=item.status,
                is_pr=item.is_pr,
                is_first_run=item.is_first_run,
                is_first_run_at_location=item.is_first_run_at_location,
                club_name=item.club_name,
                achievement_labels=item.achievement_labels,
                fetched_at=now,
            )
            db.add(row)
            try:
                with db.begin_nested():
                    db.flush()
            except IntegrityError:
                db.expunge(row)
                row = (
                    db.query(RunResult)
                    .filter(
                        RunResult.event_id == event.id,
                        RunResult.external_result_key == item.external_result_key,
                    )
                    .one_or_none()
                )
                if row is None:
                    row = (
                        db.query(RunResult)
                        .filter(
                            RunResult.event_id == event.id,
                            RunResult.participant_id == participant.id,
                        )
                        .one_or_none()
                    )
                if row is None:
                    raise
            upserted += 1
        else:
            row.participant_id = participant.id
            row.external_result_key = item.external_result_key
            if from_profile:
                if item.finish_time_sec is not None:
                    row.finish_time_sec = item.finish_time_sec
                if item.finish_time_display is not None:
                    row.finish_time_display = item.finish_time_display
                if pace_sec_per_km is not None:
                    row.pace_sec_per_km = pace_sec_per_km
                if pace_display is not None:
                    row.pace_display = pace_display
            else:
                row.position = item.position
                row.finish_time_sec = item.finish_time_sec
                row.finish_time_display = item.finish_time_display
                row.pace_sec_per_km = pace_sec_per_km
                row.pace_display = pace_display
                row.age_category = item.age_category
                row.status = item.status
                row.is_pr = item.is_pr
                row.is_first_run = item.is_first_run
                row.is_first_run_at_location = item.is_first_run_at_location
                row.club_name = item.club_name
                row.achievement_labels = item.achievement_labels
            row.fetched_at = now
            upserted += 1
    _discover_participants_after_protocol_upsert(db, platform, touched_participant_ids)
    if recalculate_pr and touched_participant_ids:
        from app.services.personal_record_service import recalculate_participants_personal_records

        recalculate_participants_personal_records(db, platform.code, touched_participant_ids)
    db.flush()
    return upserted


def _discover_participants_after_protocol_upsert(
    db: Session,
    platform: Platform,
    participant_ids: set,
) -> None:
    if not participant_ids:
        return
    from app.sync.parkrun_participant_discovery import (
        discover_parkrun_participant_from_barcode,
        enrich_parkrun_protocol_participant,
    )

    rows = db.query(Participant).filter(Participant.id.in_(participant_ids)).all()
    if platform.code == "parkrun":
        for row in rows:
            enrich_parkrun_protocol_participant(db, row, platform)
        return
    if platform.code == "s95":
        seen_barcodes: set[str] = set()
        for row in rows:
            barcode = (row.barcode_id or "").strip()
            if not barcode or barcode in seen_barcodes:
                continue
            seen_barcodes.add(barcode)
            discover_parkrun_participant_from_barcode(db, barcode)


def _volunteer_role_rank(role: str | None) -> int:
    if role is None:
        return 0
    cleaned = role.strip()
    if not cleaned:
        return 0
    if cleaned.lower() in {"volunteer", "волонтёр", "волонтер"}:
        return 1
    return 2


def _prefer_volunteer_role(existing: str | None, incoming: str | None) -> str | None:
    if _volunteer_role_rank(incoming) > _volunteer_role_rank(existing):
        return incoming.strip() if incoming else existing
    if _volunteer_role_rank(existing) >= _volunteer_role_rank(incoming):
        return existing
    return incoming


def _five_verst_profile_volunteer_tail_parts(key: str, external_user_id: str) -> tuple[str, ...] | None:
    prefix = f"{external_user_id}:"
    if not key.startswith(prefix):
        return None
    tail = key[len(prefix) :]
    if not tail:
        return None
    return tuple(tail.split(":"))


def _is_legacy_five_verst_profile_volunteer_key(key: str, external_user_id: str) -> bool:
    """Detect pre-role profile keys that collapsed multiple roles into one row per day."""
    parts = _five_verst_profile_volunteer_tail_parts(key, external_user_id)
    if parts is None:
        return False
    if len(parts) == 1 and _ISO_DATE_RE.match(parts[0]):
        return True
    if len(parts) == 2 and _ISO_DATE_RE.match(parts[0]):
        return True
    if len(parts) >= 2 and _ISO_DATE_RE.match(parts[-1]):
        return True
    return False


def _is_five_verst_profile_volunteer_external_key(key: str, external_user_id: str) -> bool:
    parts = _five_verst_profile_volunteer_tail_parts(key, external_user_id)
    if parts is None:
        return False
    if len(parts) >= 3 and _ISO_DATE_RE.match(parts[0]):
        return True
    return _is_legacy_five_verst_profile_volunteer_key(key, external_user_id)


def _purge_legacy_five_verst_profile_volunteer_keys(
    db: Session,
    participant_id: UUID,
    external_user_id: str,
) -> int:
    removed = 0
    rows = (
        db.query(VolunteerResult)
        .filter(VolunteerResult.participant_id == participant_id)
        .all()
    )
    for row in rows:
        if _is_legacy_five_verst_profile_volunteer_key(row.external_result_key, external_user_id):
            db.delete(row)
            removed += 1
    if removed:
        db.flush()
    return removed


def _is_profile_volunteer_external_key(platform_code: str, external_user_id: str, key: str) -> bool:
    if platform_code == "five_verst":
        return _is_five_verst_profile_volunteer_external_key(key, external_user_id)
    if platform_code == "s95":
        # Legacy profile keys; protocol-style keys are kept (see protocol parser).
        return key.startswith(f"vol:{external_user_id}:")
    if platform_code == "parkrun":
        return key.startswith(f"parkrun:{external_user_id}:")
    return False


def upsert_volunteer_results(
    db: Session,
    event: Event,
    platform: Platform,
    results: list[CanonicalVolunteerResult],
) -> int:
    upserted = 0
    for item in results:
        participant_id: UUID | None = None
        if item.external_user_id:
            participant = upsert_participant(
                db,
                platform,
                external_user_id=item.external_user_id,
                display_name=item.participant_name or f"User {item.external_user_id}",
            )
            participant_id = participant.id

        row = (
            db.query(VolunteerResult)
            .filter(
                VolunteerResult.event_id == event.id,
                VolunteerResult.external_result_key == item.external_result_key,
            )
            .one_or_none()
        )
        now = datetime.now(timezone.utc)
        if row is None:
            row = VolunteerResult(
                event_id=event.id,
                participant_id=participant_id,
                external_result_key=item.external_result_key,
                role=item.role,
                fetched_at=now,
            )
            db.add(row)
            upserted += 1
        else:
            row.participant_id = participant_id
            row.role = _prefer_volunteer_role(row.role, item.role)
            row.fetched_at = now
            upserted += 1
    db.flush()
    return upserted


def _profile_event_title(location_name: str, event_number: int | None) -> str:
    if event_number is not None:
        return f"{location_name} #{event_number}"
    return location_name


def _normalize_location_slug(
    location_external_key: str | None,
    location_name: str | None,
) -> str:
    if location_external_key and location_external_key.strip():
        return location_external_key.strip()
    if location_name and location_name.strip():
        return location_name.strip().lower().replace(" ", "_")
    return "unknown"


def _profile_external_event_key(event_date: date, location_slug: str) -> str:
    return f"{event_date.isoformat()}:{location_slug}"


def _find_event_by_location_date(
    db: Session,
    platform: Platform,
    location_id: UUID,
    event_date: date,
) -> Event | None:
    return (
        db.query(Event)
        .filter(
            Event.platform_id == platform.id,
            Event.location_id == location_id,
            Event.event_date == event_date,
        )
        .order_by(Event.created_at.asc())
        .first()
    )


def _find_existing_event(
    db: Session,
    platform: Platform,
    location: Location,
    *,
    event_date: date,
    external_event_key: str,
    location_slug: str,
    location_name: str,
) -> Event | None:
    row = _find_event_by_location_date(db, platform, location.id, event_date)
    if row is not None:
        return row

    row = (
        db.query(Event)
        .filter(
            Event.platform_id == platform.id,
            Event.external_event_key == external_event_key,
        )
        .one_or_none()
    )
    if row is not None:
        return row

    normalized_name = (location_name or "").strip().lower()
    location_filters = [
        Location.external_key == location_slug,
        func.lower(Location.external_key) == location_slug.lower(),
    ]
    if normalized_name:
        location_filters.append(func.lower(Location.name) == normalized_name)

    alt_location_ids = [
        loc_id
        for (loc_id,) in db.query(Location.id)
        .filter(Location.platform_id == platform.id, or_(*location_filters))
        .all()
    ]
    for loc_id in alt_location_ids:
        row = _find_event_by_location_date(db, platform, loc_id, event_date)
        if row is not None:
            return row
    return None


def _assign_external_event_key(
    db: Session,
    platform: Platform,
    row: Event,
    external_event_key: str,
) -> None:
    if row.external_event_key == external_event_key:
        return
    conflict = (
        db.query(Event.id)
        .filter(
            Event.platform_id == platform.id,
            Event.external_event_key == external_event_key,
            Event.id != row.id,
        )
        .one_or_none()
    )
    if conflict is None:
        row.external_event_key = external_event_key


def _apply_event_fields(
    row: Event,
    *,
    platform_code: str,
    location: Location,
    event_date: date,
    event_number: int | None,
    is_test_event: bool,
    title: str,
    source_url: str | None,
    finishers_count: int | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    row.location_id = location.id
    row.event_date = event_date
    row.event_number = event_number
    row.is_test_event = is_test_event
    row.title = title
    preferred_source_url = prefer_event_source_url(platform_code, row.source_url, source_url)
    if preferred_source_url:
        row.source_url = preferred_source_url
    if finishers_count is not None:
        row.finishers_count = finishers_count
        row.runners_count = finishers_count
    row.fetched_at = now
    row.sync_status = SyncStatus.ok
    row.error_message = None
    row.parser_version = PARSER_VERSION


def upsert_event_for_profile(
    db: Session,
    platform: Platform,
    location: Location,
    *,
    external_event_key: str,
    event_date: date,
    event_number: int | None,
    location_name: str,
    location_slug: str,
    source_url: str,
    is_test_event: bool = False,
) -> Event:
    row = _find_existing_event(
        db,
        platform,
        location,
        event_date=event_date,
        external_event_key=external_event_key,
        location_slug=location_slug,
        location_name=location_name,
    )
    now = datetime.now(timezone.utc)
    title = _profile_event_title(location_name, event_number)
    if is_test_event:
        title = f"{location_name} (тестовый)"

    if row is None:
        row = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=external_event_key,
            event_date=event_date,
            event_number=event_number,
            is_test_event=is_test_event,
            title=title,
            source_url=source_url,
            parser_version=PARSER_VERSION,
            fetched_at=now,
            sync_status=SyncStatus.ok,
        )
        db.add(row)
        try:
            with db.begin_nested():
                db.flush()
        except IntegrityError:
            db.expunge(row)
            row = _find_existing_event(
                db,
                platform,
                location,
                event_date=event_date,
                external_event_key=external_event_key,
                location_slug=location_slug,
                location_name=location_name,
            )
            if row is None:
                raise

    _assign_external_event_key(db, platform, row, external_event_key)
    _apply_event_fields(
        row,
        platform_code=platform.code,
        location=location,
        event_date=event_date,
        event_number=event_number,
        is_test_event=is_test_event,
        title=title,
        source_url=source_url,
    )

    try:
        db.flush()
    except IntegrityError:
        db.expire(row)
        row = _find_existing_event(
            db,
            platform,
            location,
            event_date=event_date,
            external_event_key=external_event_key,
            location_slug=location_slug,
            location_name=location_name,
        )
        if row is None:
            raise
        _assign_external_event_key(db, platform, row, external_event_key)
        _apply_event_fields(
            row,
            platform_code=platform.code,
            location=location,
            event_date=event_date,
            event_number=event_number,
            is_test_event=is_test_event,
            title=title,
            source_url=source_url,
        )
        db.flush()
    return row


def import_profile_run_results(
    db: Session,
    platform: Platform,
    results: list[CanonicalRunResult],
) -> int:
    imported = 0
    touched_participant_ids: set[UUID] = set()
    for item in results:
        slug = _normalize_location_slug(item.location_external_key, item.location_name)
        display_name = item.location_name or slug
        if platform.code == "s95":
            location_source_url = f"https://s95.ru/events/{slug}" if slug != "unknown" else None
            country = "Россия"
        elif platform.code == "parkrun":
            location_source_url = (
                f"https://www.parkrun.org.uk/{slug}/" if slug != "unknown" else None
            )
            country = "United Kingdom"
        else:
            location_source_url = f"https://5verst.ru/{slug}/" if slug != "unknown" else None
            country = "Россия"
        location, _ = upsert_location(
            db,
            platform,
            CanonicalLocation(
                external_key=slug,
                name=display_name,
                country=country,
                source_url=location_source_url,
            ),
        )
        external_event_key = _profile_external_event_key(item.event_date, slug)
        if platform.code == "s95":
            source_url = (
                f"https://s95.ru/events/{slug}"
                if slug != "unknown"
                else ""
            )
        elif platform.code == "parkrun":
            source_url = (
                f"https://www.parkrun.org.uk/{slug}/results/{item.event_number or ''}/"
                if slug != "unknown"
                else ""
            )
        else:
            source_url = (
                f"https://5verst.ru/{slug}/results/{item.event_date.strftime('%d.%m.%Y')}/"
                if slug != "unknown"
                else ""
            )
        event = upsert_event_for_profile(
            db,
            platform,
            location,
            external_event_key=external_event_key,
            event_date=item.event_date,
            event_number=item.event_number,
            location_name=display_name,
            location_slug=slug,
            source_url=source_url,
        )
        imported += upsert_run_results(
            db,
            event,
            platform,
            [item],
            from_profile=True,
            recalculate_pr=False,
        )
        participant = (
            db.query(Participant)
            .filter(
                Participant.platform_id == platform.id,
                Participant.external_user_id == item.external_user_id,
            )
            .one_or_none()
        )
        if participant is not None:
            touched_participant_ids.add(participant.id)
    if platform.code == "five_verst":
        participant_ids = {item.external_user_id for item in results}
        for external_user_id in participant_ids:
            participant = (
                db.query(Participant)
                .filter(
                    Participant.platform_id == platform.id,
                    Participant.external_user_id == external_user_id,
                )
                .one_or_none()
            )
            if participant is not None:
                dedupe_five_verst_run_results_in_db(db, platform.id, participant.id)
    if touched_participant_ids:
        from app.services.personal_record_service import recalculate_participants_personal_records

        recalculate_participants_personal_records(db, platform.code, touched_participant_ids)
    db.flush()
    return imported


def _volunteer_role_dedupe_key(role: str | None) -> str:
    if not role:
        return "volunteer"
    normalized = re.sub(r"[^\w]+", "_", role.lower(), flags=re.UNICODE).strip("_")
    return normalized or "volunteer"


def _volunteer_result_completeness(vol: VolunteerResult) -> tuple[int, int, int]:
    key = vol.external_result_key or ""
    is_protocol = 1 if ":vol:" in key else 0
    return (
        -is_protocol,
        0 if vol.role else 1,
        0 if vol.fetched_at else 1,
    )


def dedupe_participant_volunteer_results(
    db: Session,
    platform_id: UUID,
    participant_id: UUID,
) -> int:
    """One volunteering row per date, location and role."""
    rows = (
        db.query(VolunteerResult, Event)
        .join(Event, VolunteerResult.event_id == Event.id)
        .filter(
            VolunteerResult.participant_id == participant_id,
            Event.platform_id == platform_id,
        )
        .all()
    )
    groups: dict[tuple[date, UUID, str], list[tuple[VolunteerResult, Event]]] = defaultdict(list)
    for vol, event in rows:
        groups[(event.event_date, event.location_id, _volunteer_role_dedupe_key(vol.role))].append(
            (vol, event)
        )

    deleted = 0
    touched_event_ids: set[UUID] = set()
    for group in groups.values():
        if len(group) <= 1:
            continue
        group.sort(key=lambda pair: _volunteer_result_completeness(pair[0]))
        for vol, event in group[1:]:
            touched_event_ids.add(event.id)
            db.delete(vol)
            deleted += 1

    for event_id in touched_event_ids:
        has_runs = db.query(RunResult).filter(RunResult.event_id == event_id).count() > 0
        has_vol = db.query(VolunteerResult).filter(VolunteerResult.event_id == event_id).count() > 0
        if not has_runs and not has_vol:
            db.query(Event).filter(Event.id == event_id).delete()

    if deleted:
        db.flush()
    return deleted


def dedupe_five_verst_run_results_in_db(
    db: Session,
    platform_id: UUID,
    participant_id: UUID,
) -> int:
    """One run per participant per event_date per location; remove extras and orphan events."""
    rows = (
        db.query(RunResult, Event)
        .join(Event, RunResult.event_id == Event.id)
        .filter(
            RunResult.participant_id == participant_id,
            Event.platform_id == platform_id,
        )
        .all()
    )
    groups: dict[tuple[date, UUID], list[tuple[RunResult, Event]]] = defaultdict(list)
    for run, event in rows:
        groups[(event.event_date, event.location_id)].append((run, event))

    deleted = 0
    touched_event_ids: set[UUID] = set()
    for group in groups.values():
        if len(group) <= 1:
            continue
        group.sort(
            key=lambda pair: (
                pair[0].position is None,
                pair[0].age_category is None,
                pair[0].finish_time_sec is None,
                pair[0].fetched_at is None,
            ),
        )
        for run, event in group[1:]:
            touched_event_ids.add(event.id)
            db.delete(run)
            deleted += 1

    for event_id in touched_event_ids:
        has_runs = db.query(RunResult).filter(RunResult.event_id == event_id).count() > 0
        has_vol = db.query(VolunteerResult).filter(VolunteerResult.event_id == event_id).count() > 0
        if not has_runs and not has_vol:
            db.query(Event).filter(Event.id == event_id).delete()

    if deleted:
        db.flush()
    return deleted


def import_profile_volunteer_results(
    db: Session,
    platform: Platform,
    participant_id: UUID,
    results: list[CanonicalVolunteerResult],
) -> int:
    if not results:
        return 0

    participant = db.query(Participant).filter(Participant.id == participant_id).one()
    if platform.code == "five_verst":
        _purge_legacy_five_verst_profile_volunteer_keys(
            db,
            participant_id,
            participant.external_user_id,
        )

    incoming_keys: set[str] = set()
    imported = 0
    for item in results:
        slug = _normalize_location_slug(item.location_external_key, item.location_name)
        display_name = item.location_name or slug
        if platform.code == "parkrun":
            country = "United Kingdom"
            default_source = f"https://www.parkrun.org.uk/{slug}/" if slug != "unknown" else ""
        elif platform.code == "s95":
            country = "Россия"
            default_source = f"https://s95.ru/events/{slug}" if slug != "unknown" else ""
        else:
            country = "Россия"
            default_source = f"https://5verst.ru/{slug}/" if slug != "unknown" else ""
        location, _ = upsert_location(
            db,
            platform,
            CanonicalLocation(
                external_key=slug,
                name=display_name,
                country=country,
                source_url=default_source,
            ),
        )
        external_event_key = _profile_external_event_key(item.event_date, slug)
        source_url = item.source_url or (
            f"https://5verst.ru/{slug}/results/{item.event_date.strftime('%d.%m.%Y')}/"
            if platform.code == "five_verst" and slug != "unknown"
            else default_source
        )
        event = upsert_event_for_profile(
            db,
            platform,
            location,
            external_event_key=external_event_key,
            event_date=item.event_date,
            event_number=item.event_number,
            location_name=display_name,
            location_slug=slug,
            source_url=source_url,
        )
        incoming_keys.add(item.external_result_key)
        imported += upsert_volunteer_results(db, event, platform, [item])

    dedupe_participant_volunteer_results(db, platform.id, participant_id)

    stale_rows = (
        db.query(VolunteerResult)
        .filter(VolunteerResult.participant_id == participant_id)
        .all()
    )
    for row in stale_rows:
        if row.external_result_key in incoming_keys:
            continue
        if _is_profile_volunteer_external_key(
            platform.code,
            participant.external_user_id,
            row.external_result_key,
        ):
            db.delete(row)

    db.flush()
    return imported
