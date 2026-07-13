"""Tests for is_first_run / is_first_run_at_location derivation."""
from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import EventCrosslink, Location, Participant, Platform, RunResult
from app.services.personal_record_service import (
    recalculate_first_run_flags,
    recalculate_participants_first_run_flags,
)
from app.sync import upsert


@pytest.fixture
def s95_platform(db_session: Session) -> Platform:
    return upsert.get_platform(db_session, "s95")


@pytest.fixture
def s95_location(db_session: Session, s95_platform: Platform) -> Location:
    from app.platform_adapters.canonical import CanonicalLocation

    location, _ = upsert.upsert_location(
        db_session,
        s95_platform,
        CanonicalLocation(external_key="zil", name="ЗИЛ"),
    )
    db_session.flush()
    return location


@pytest.fixture
def s95_location_2(db_session: Session, s95_platform: Platform) -> Location:
    from app.platform_adapters.canonical import CanonicalLocation

    location, _ = upsert.upsert_location(
        db_session,
        s95_platform,
        CanonicalLocation(external_key="fili", name="Фили"),
    )
    db_session.flush()
    return location


def _add_run(
    db: Session,
    *,
    platform: Platform,
    location: Location,
    participant: Participant,
    event_date: date,
    finish_time_sec: int = 1700,
) -> RunResult:
    from app.platform_adapters.canonical import CanonicalEventSummary

    external_event_key = f"{location.external_key}:{event_date.isoformat()}"
    summary, _ = upsert.upsert_event_summary(
        db,
        platform,
        location,
        CanonicalEventSummary(
            external_event_key=external_event_key,
            event_date=event_date,
            event_number=None,
            location_external_key=location.external_key,
            location_name=location.name,
            source_url=f"https://s95.ru/events/{location.external_key}/{event_date.isoformat()}",
            summary_hash=f"hash-{external_event_key}",
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
        external_result_key=f"test:{participant.id}:{external_event_key}",
    )
    db.add(run)
    db.flush()
    return run


def test_first_run_and_first_at_location_flags(
    db_session: Session,
    s95_platform: Platform,
    s95_location: Location,
    s95_location_2: Location,
) -> None:
    participant = Participant(
        id=uuid4(),
        platform_id=s95_platform.id,
        external_user_id=f"s95-first-{uuid4().hex[:8]}",
        display_name="S95 Runner",
    )
    db_session.add(participant)
    db_session.flush()

    first = _add_run(
        db_session, platform=s95_platform, location=s95_location,
        participant=participant, event_date=date(2022, 1, 1),
    )
    repeat_same_location = _add_run(
        db_session, platform=s95_platform, location=s95_location,
        participant=participant, event_date=date(2022, 2, 1),
    )
    first_at_new_location = _add_run(
        db_session, platform=s95_platform, location=s95_location_2,
        participant=participant, event_date=date(2022, 3, 1),
    )

    stats = recalculate_first_run_flags(db_session, "s95", participant_id=participant.id)
    db_session.flush()

    assert stats["participants_touched"] == 1
    assert first.is_first_run is True
    assert first.is_first_run_at_location is True
    assert repeat_same_location.is_first_run is False
    assert repeat_same_location.is_first_run_at_location is False
    assert first_at_new_location.is_first_run is False
    assert first_at_new_location.is_first_run_at_location is True


def test_secondary_crosslink_duplicate_never_gets_first_run_flags(
    db_session: Session,
    s95_platform: Platform,
    s95_location: Location,
) -> None:
    participant = Participant(
        id=uuid4(),
        platform_id=s95_platform.id,
        external_user_id=f"s95-dup-{uuid4().hex[:8]}",
        display_name="Dup Runner",
    )
    db_session.add(participant)
    db_session.flush()

    counted = _add_run(
        db_session, platform=s95_platform, location=s95_location,
        participant=participant, event_date=date(2022, 2, 1),
    )
    earlier_duplicate = _add_run(
        db_session, platform=s95_platform, location=s95_location,
        participant=participant, event_date=date(2022, 1, 1),
    )
    db_session.add(
        EventCrosslink(primary_event_id=counted.event_id, secondary_event_id=earlier_duplicate.event_id)
    )
    db_session.flush()

    recalculate_first_run_flags(db_session, "s95", participant_id=participant.id)
    db_session.flush()

    assert earlier_duplicate.is_first_run is False
    assert earlier_duplicate.is_first_run_at_location is False
    assert counted.is_first_run is True
    assert counted.is_first_run_at_location is True


def test_recalculate_participants_first_run_flags_skips_five_verst(
    db_session: Session,
) -> None:
    """five_verst already gets is_first_run from site achievement badges — the derived
    recalculation must not run for it and overwrite that authoritative value."""
    five_verst_platform = upsert.get_platform(db_session, "five_verst")
    from app.platform_adapters.canonical import CanonicalLocation

    location, _ = upsert.upsert_location(
        db_session,
        five_verst_platform,
        CanonicalLocation(external_key="natashinsky", name="Наташинский"),
    )
    db_session.flush()

    participant = Participant(
        id=uuid4(),
        platform_id=five_verst_platform.id,
        external_user_id=f"5v-{uuid4().hex[:8]}",
        display_name="5verst Runner",
    )
    db_session.add(participant)
    db_session.flush()

    run = _add_run(
        db_session, platform=five_verst_platform, location=location,
        participant=participant, event_date=date(2022, 1, 1),
    )
    assert run.is_first_run is False

    recalculate_participants_first_run_flags(db_session, "five_verst", {participant.id})
    db_session.flush()

    # Untouched: recalculate_participants_first_run_flags is a no-op for five_verst.
    assert run.is_first_run is False
