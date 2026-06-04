from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.activity_date import has_real_activity_date
from app.models import Event, Location, Participant, Platform, RunResult, VolunteerResult
from app.platform_adapters.canonical import (
    CanonicalRunResult,
    CanonicalVolunteerResult,
    ProfilePreview,
)
from app.platform_adapters.five_verst.url import InvalidProfileUrlError as FvInvalidProfileUrlError
from app.platform_adapters.five_verst.url import normalize_profile_url as parse_five_verst_input
from app.platform_adapters.parkrun.url import InvalidProfileUrlError as ParkrunInvalidProfileUrlError
from app.platform_adapters.parkrun.url import parse_profile_input as parse_parkrun_input
from app.platform_adapters.s95.url import InvalidProfileUrlError as S95InvalidProfileUrlError
from app.platform_adapters.s95.url import parse_athlete_url as parse_s95_input
from app.profile_preview import build_recent_preview_activities
from app.s95.parkrun import is_parkrun_eligible_barcode
from app.volunteering_occasions import count_volunteering_for_platform


@dataclass(frozen=True)
class ResolvedProfileIdentity:
    external_user_id: str
    canonical_profile_url: str


@dataclass(frozen=True)
class ParticipantDataFreshness:
    data_updated_at: datetime | None
    data_through_date: date | None


def resolve_profile_identity(platform_code: str, profile_url: str) -> ResolvedProfileIdentity:
    if platform_code == "five_verst":
        parsed = parse_five_verst_input(profile_url)
        return ResolvedProfileIdentity(
            external_user_id=parsed.user_id,
            canonical_profile_url=parsed.canonical_url,
        )
    if platform_code == "s95":
        parsed = parse_s95_input(profile_url)
        return ResolvedProfileIdentity(
            external_user_id=parsed.external_user_id,
            canonical_profile_url=parsed.canonical_url,
        )
    if platform_code == "parkrun":
        parsed = parse_parkrun_input(profile_url)
        return ResolvedProfileIdentity(
            external_user_id=parsed.athlete_id,
            canonical_profile_url=parsed.profile_url,
        )
    raise ValueError(f"Unsupported platform: {platform_code}")


def find_participant_by_identity(
    db: Session,
    platform: Platform,
    external_user_id: str,
) -> Participant | None:
    return (
        db.query(Participant)
        .filter(
            Participant.platform_id == platform.id,
            Participant.external_user_id == external_user_id,
        )
        .one_or_none()
    )


def _max_datetime(*values: datetime | None) -> datetime | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _max_date(*values: date | None) -> date | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def get_participant_data_freshness(
    db: Session,
    participant: Participant,
    *,
    platform_code: str,
    platform_id: UUID,
) -> ParticipantDataFreshness:
    runs_max_date = (
        db.query(func.max(Event.event_date))
        .select_from(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .filter(
            RunResult.participant_id == participant.id,
            Event.platform_id == platform_id,
            Event.is_test_event.is_(False),
        )
        .scalar()
    )
    vol_max_date = (
        db.query(func.max(Event.event_date))
        .select_from(VolunteerResult)
        .join(Event, VolunteerResult.event_id == Event.id)
        .filter(
            VolunteerResult.participant_id == participant.id,
            Event.platform_id == platform_id,
            Event.is_test_event.is_(False),
            Event.event_date > date(1970, 1, 1),
        )
        .scalar()
    )
    run_fetched_at = (
        db.query(func.max(RunResult.fetched_at))
        .filter(RunResult.participant_id == participant.id)
        .scalar()
    )
    vol_fetched_at = (
        db.query(func.max(VolunteerResult.fetched_at))
        .filter(VolunteerResult.participant_id == participant.id)
        .scalar()
    )
    return ParticipantDataFreshness(
        data_updated_at=_max_datetime(participant.fetched_at, run_fetched_at, vol_fetched_at),
        data_through_date=_max_date(runs_max_date, vol_max_date),
    )


def _count_participant_runs(db: Session, participant_id: UUID, platform_id: UUID) -> int:
    return (
        db.query(func.count(RunResult.id))
        .join(Event, RunResult.event_id == Event.id)
        .filter(
            RunResult.participant_id == participant_id,
            Event.platform_id == platform_id,
            Event.is_test_event.is_(False),
        )
        .scalar()
        or 0
    )


def _count_participant_volunteering(
    db: Session,
    participant: Participant,
    *,
    platform_code: str,
    platform_id: UUID,
) -> int:
    vol_rows = (
        db.query(Event.event_date, Location.external_key)
        .select_from(VolunteerResult)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .filter(
            VolunteerResult.participant_id == participant.id,
            Event.platform_id == platform_id,
            Event.is_test_event.is_(False),
            Event.event_date > date(1970, 1, 1),
        )
        .all()
    )
    db_count = count_volunteering_for_platform(platform_code, vol_rows)
    if platform_code == "parkrun":
        extra_total = (participant.profile_extra or {}).get("volunteer_occasions_total")
        if isinstance(extra_total, int) and extra_total > db_count:
            return extra_total
    return db_count


def _load_recent_runs(
    db: Session,
    participant_id: UUID,
    platform_id: UUID,
    *,
    limit: int = 20,
) -> list[CanonicalRunResult]:
    rows = (
        db.query(RunResult, Event, Location)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .filter(
            RunResult.participant_id == participant_id,
            Event.platform_id == platform_id,
            Event.is_test_event.is_(False),
        )
        .order_by(Event.event_date.desc(), RunResult.position.asc().nullslast())
        .limit(limit)
        .all()
    )
    results: list[CanonicalRunResult] = []
    for run, event, location in rows:
        results.append(
            CanonicalRunResult(
                external_result_key=run.external_result_key,
                event_date=event.event_date,
                external_user_id="",
                participant_name="",
                position=run.position,
                finish_time_sec=run.finish_time_sec,
                finish_time_display=run.finish_time_display,
                pace_sec_per_km=run.pace_sec_per_km,
                pace_display=run.pace_display,
                location_external_key=location.external_key,
                location_name=location.name,
                event_number=event.event_number,
            )
        )
    return results


def _load_recent_volunteering(
    db: Session,
    participant_id: UUID,
    platform_id: UUID,
    *,
    limit: int = 20,
) -> list[CanonicalVolunteerResult]:
    rows = (
        db.query(VolunteerResult, Event, Location)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .filter(
            VolunteerResult.participant_id == participant_id,
            Event.platform_id == platform_id,
            Event.is_test_event.is_(False),
            Event.event_date > date(1970, 1, 1),
        )
        .order_by(Event.event_date.desc())
        .limit(limit)
        .all()
    )
    results: list[CanonicalVolunteerResult] = []
    for vol, event, location in rows:
        if not has_real_activity_date(event.event_date):
            continue
        results.append(
            CanonicalVolunteerResult(
                external_result_key=vol.external_result_key,
                event_date=event.event_date,
                external_user_id=None,
                participant_name=None,
                role=vol.role or "",
                location_external_key=location.external_key,
                location_name=location.name,
                event_number=event.event_number,
            )
        )
    return results


def build_profile_preview_from_db(
    db: Session,
    platform: Platform,
    participant: Participant,
    *,
    identity: ResolvedProfileIdentity,
) -> ProfilePreview | None:
    display_name = (participant.display_name or "").strip()
    if not display_name:
        return None

    total_runs = _count_participant_runs(db, participant.id, platform.id)
    total_volunteering = _count_participant_volunteering(
        db,
        participant,
        platform_code=platform.code,
        platform_id=platform.id,
    )
    runs = _load_recent_runs(db, participant.id, platform.id)
    volunteering = _load_recent_volunteering(db, participant.id, platform.id)
    freshness = get_participant_data_freshness(
        db,
        participant,
        platform_code=platform.code,
        platform_id=platform.id,
    )

    profile_url = participant.profile_url or identity.canonical_profile_url
    age_category = participant.age_category
    if platform.code == "parkrun" and not age_category:
        extra_age = (participant.profile_extra or {}).get("age_category")
        if isinstance(extra_age, str) and extra_age.strip():
            age_category = extra_age.strip()

    return ProfilePreview(
        external_user_id=participant.external_user_id,
        display_name=display_name,
        profile_url=profile_url,
        total_runs=total_runs,
        total_volunteering=total_volunteering,
        club_name=participant.club_name,
        barcode_id=participant.barcode_id,
        planning_location=participant.planning_location,
        planning_location_seen_at=participant.planning_location_seen_at,
        age_category=age_category,
        parkrun_eligible=platform.code == "s95" and is_parkrun_eligible_barcode(participant.barcode_id),
        platform_code=platform.code,
        recent_activities=build_recent_preview_activities(runs, volunteering),
        data_source="database",
        data_updated_at=freshness.data_updated_at,
        data_through_date=freshness.data_through_date,
    )


def try_profile_preview_from_db(
    db: Session,
    platform_code: str,
    profile_url: str,
) -> ProfilePreview | None:
    try:
        identity = resolve_profile_identity(platform_code, profile_url)
    except (FvInvalidProfileUrlError, S95InvalidProfileUrlError, ParkrunInvalidProfileUrlError, ValueError):
        return None

    platform = db.query(Platform).filter(Platform.code == platform_code).one_or_none()
    if platform is None:
        return None

    participant = find_participant_by_identity(db, platform, identity.external_user_id)
    if participant is None:
        return None

    return build_profile_preview_from_db(db, platform, participant, identity=identity)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
