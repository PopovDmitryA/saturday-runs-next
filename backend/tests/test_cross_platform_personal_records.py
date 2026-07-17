"""Tests for is_global_pr / is_location_pr recalculation (athlete-wide, across platforms)."""
from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import (
    EventCrosslink,
    Location,
    LocationCatalog,
    LocationCatalogLink,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
)
from app.platform_adapters.canonical import CanonicalEventSummary
from app.services.personal_record_service import recalculate_cross_platform_personal_records
from app.sync import upsert


def _make_user(db: Session) -> User:
    user = User(consent_accepted=True, display_name="Cross Platform Tester")
    db.add(user)
    db.flush()
    return user


def _make_participant(db: Session, platform: Platform, *, suffix: str) -> Participant:
    participant = Participant(
        id=uuid4(),
        platform_id=platform.id,
        external_user_id=f"{platform.code}-{suffix}",
        display_name="Cross Platform Tester",
    )
    db.add(participant)
    db.flush()
    return participant


def _link(db: Session, user: User, platform: Platform, participant: Participant) -> None:
    db.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url=f"https://example.test/{participant.external_user_id}",
        )
    )
    db.flush()


def _add_run(
    db: Session,
    *,
    platform: Platform,
    location: Location,
    participant: Participant,
    event_date: date,
    finish_time_sec: int,
) -> RunResult:
    summary, _ = upsert.upsert_event_summary(
        db,
        platform,
        location,
        CanonicalEventSummary(
            external_event_key=f"{location.external_key}:{event_date.isoformat()}",
            event_date=event_date,
            event_number=None,
            location_external_key=location.external_key,
            location_name=location.name,
            source_url=f"https://example.test/events/{location.external_key}/{event_date.isoformat()}",
            summary_hash=f"hash-{location.external_key}-{event_date.isoformat()}",
        ),
    )
    event = upsert.upsert_event_for_summary(
        db,
        platform,
        location,
        CanonicalEventSummary(
            external_event_key=summary.external_event_key,
            event_date=event_date,
            event_number=None,
            location_external_key=location.external_key,
            location_name=location.name,
            source_url=summary.source_url or "",
            summary_hash=summary.summary_hash,
        ),
        summary,
    )
    run = RunResult(
        id=uuid4(),
        event_id=event.id,
        participant_id=participant.id,
        finish_time_sec=finish_time_sec,
        finish_time_display=f"00:{finish_time_sec // 60:02d}:{finish_time_sec % 60:02d}",
        external_result_key=f"test:{participant.id}:{event_date.isoformat()}",
    )
    db.add(run)
    db.flush()
    return run


@pytest.fixture
def five_verst_platform(db_session: Session) -> Platform:
    return upsert.get_platform(db_session, "five_verst")


@pytest.fixture
def s95_platform(db_session: Session) -> Platform:
    return upsert.get_platform(db_session, "s95")


def _make_location(db: Session, platform: Platform, *, external_key: str, name: str) -> Location:
    from app.platform_adapters.canonical import CanonicalLocation

    location, _ = upsert.upsert_location(
        db,
        platform,
        CanonicalLocation(external_key=external_key, name=name),
    )
    db.flush()
    return location


def test_same_catalog_location_across_platforms_tracks_one_record(
    db_session: Session,
    five_verst_platform: Platform,
    s95_platform: Platform,
) -> None:
    """A physical location run under two systems shares one location record: the
    first-ever run there is never a location PR, only a later faster run is —
    regardless of which system it happened on."""
    suffix = uuid4().hex[:8]
    user = _make_user(db_session)

    five_verst_location = _make_location(
        db_session, five_verst_platform, external_key=f"zil-5v-{suffix}", name="ЗИЛ"
    )
    s95_location = _make_location(db_session, s95_platform, external_key=f"zil-s95-{suffix}", name="ЗИЛ")

    catalog = LocationCatalog(canonical_name="ЗИЛ", active_platform="five_verst", is_closed=False)
    db_session.add(catalog)
    db_session.flush()
    db_session.add_all(
        [
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=five_verst_platform.id,
                external_key=five_verst_location.external_key,
                location_id=five_verst_location.id,
            ),
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=s95_platform.id,
                external_key=s95_location.external_key,
                location_id=s95_location.id,
            ),
        ]
    )
    db_session.flush()

    five_verst_participant = _make_participant(db_session, five_verst_platform, suffix=suffix)
    s95_participant = _make_participant(db_session, s95_platform, suffix=suffix)
    _link(db_session, user, five_verst_platform, five_verst_participant)
    _link(db_session, user, s95_platform, s95_participant)

    first = _add_run(
        db_session,
        platform=five_verst_platform,
        location=five_verst_location,
        participant=five_verst_participant,
        event_date=date(2023, 1, 1),
        finish_time_sec=1200,
    )
    faster_other_system = _add_run(
        db_session,
        platform=s95_platform,
        location=s95_location,
        participant=s95_participant,
        event_date=date(2023, 6, 1),
        finish_time_sec=1100,
    )
    slower = _add_run(
        db_session,
        platform=five_verst_platform,
        location=five_verst_location,
        participant=five_verst_participant,
        event_date=date(2024, 1, 1),
        finish_time_sec=1150,
    )

    recalculate_cross_platform_personal_records(db_session, user.id)
    db_session.flush()

    assert first.is_global_pr is False  # very first run ever is a baseline, not a global PR
    assert first.is_location_pr is False  # first-ever run at the location is never a location PR

    assert faster_other_system.is_global_pr is True
    assert faster_other_system.is_location_pr is True  # beat the standing best, on a different system

    assert slower.is_global_pr is False
    assert slower.is_location_pr is False


def test_secondary_crosslink_duplicate_excluded_from_cross_platform_records(
    db_session: Session,
    five_verst_platform: Platform,
    s95_platform: Platform,
) -> None:
    suffix = uuid4().hex[:8]
    user = _make_user(db_session)

    five_verst_location = _make_location(
        db_session, five_verst_platform, external_key=f"dup-5v-{suffix}", name="Дубль"
    )
    s95_location = _make_location(db_session, s95_platform, external_key=f"dup-s95-{suffix}", name="Дубль")

    five_verst_participant = _make_participant(db_session, five_verst_platform, suffix=suffix)
    s95_participant = _make_participant(db_session, s95_platform, suffix=suffix)
    _link(db_session, user, five_verst_platform, five_verst_participant)
    _link(db_session, user, s95_platform, s95_participant)

    counted = _add_run(
        db_session,
        platform=five_verst_platform,
        location=five_verst_location,
        participant=five_verst_participant,
        event_date=date(2023, 1, 1),
        finish_time_sec=1200,
    )
    duplicate = _add_run(
        db_session,
        platform=s95_platform,
        location=s95_location,
        participant=s95_participant,
        event_date=date(2023, 1, 1),
        finish_time_sec=1100,  # faster, but it's a "не в зачёте" duplicate of `counted`
    )
    db_session.add(
        EventCrosslink(primary_event_id=counted.event_id, secondary_event_id=duplicate.event_id)
    )
    db_session.flush()

    recalculate_cross_platform_personal_records(db_session, user.id)
    db_session.flush()

    assert duplicate.is_global_pr is False
    assert duplicate.is_location_pr is False
    assert counted.is_global_pr is False  # only run that counts — a baseline, not a record
    assert counted.is_location_pr is False  # only run that counts, and it's the first one


def test_unrelated_locations_tracked_independently(
    db_session: Session,
    five_verst_platform: Platform,
) -> None:
    suffix = uuid4().hex[:8]
    user = _make_user(db_session)
    location_a = _make_location(db_session, five_verst_platform, external_key=f"loc-a-{suffix}", name="Park A")
    location_b = _make_location(db_session, five_verst_platform, external_key=f"loc-b-{suffix}", name="Park B")
    participant = _make_participant(db_session, five_verst_platform, suffix=suffix)
    _link(db_session, user, five_verst_platform, participant)

    first_a = _add_run(
        db_session,
        platform=five_verst_platform,
        location=location_a,
        participant=participant,
        event_date=date(2023, 1, 1),
        finish_time_sec=1200,
    )
    first_b = _add_run(
        db_session,
        platform=five_verst_platform,
        location=location_b,
        participant=participant,
        event_date=date(2023, 2, 1),
        finish_time_sec=1300,  # slower than first_a, but a different, unrelated location
    )

    recalculate_cross_platform_personal_records(db_session, user.id)
    db_session.flush()

    assert first_a.is_global_pr is False  # very first run ever — baseline, not a record
    assert first_a.is_location_pr is False  # first at its own location

    assert first_b.is_global_pr is False  # slower than the athlete's all-time best
    assert first_b.is_location_pr is False  # still first at ITS location, not a location PR either
