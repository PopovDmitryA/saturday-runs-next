from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, RunResult, SyncStatus
from app.platform_adapters.canonical import CanonicalParticipant, CanonicalRunResult
from app.sync.s95_athlete_mismatch import detect_profile_run_mismatches
from app.sync.s95_participant_sync import apply_s95_participant_profile


def test_apply_s95_participant_profile_sets_planning_seen_at() -> None:
    row = Participant(
        platform_id=uuid4(),
        external_user_id="5207",
        display_name="Test",
    )
    observed_at = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
    apply_s95_participant_profile(
        row,
        CanonicalParticipant(
            external_user_id="5207",
            display_name="Test",
            profile_url="https://s95.ru/athletes/5207/",
            planning_location="Парк Горького",
        ),
        now=observed_at,
    )
    assert row.planning_location == "Парк Горького"
    assert row.planning_location_seen_at == observed_at
    assert row.fetched_at == observed_at
    assert row.sync_status == SyncStatus.ok


def test_apply_s95_participant_profile_clears_planning_seen_at() -> None:
    row = Participant(
        platform_id=uuid4(),
        external_user_id="5207",
        display_name="Test",
        planning_location="Old",
        planning_location_seen_at=datetime.now(timezone.utc),
    )
    apply_s95_participant_profile(
        row,
        CanonicalParticipant(
            external_user_id="5207",
            display_name="Test",
            profile_url="https://s95.ru/athletes/5207/",
            planning_location=None,
        ),
    )
    assert row.planning_location is None
    assert row.planning_location_seen_at is None


def test_detect_profile_run_mismatch_by_position(db_session: Session) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if platform is None:
        pytest.skip("s95 platform not seeded")

    slug = f"mismatch-{uuid4().hex[:8]}"
    location = Location(platform_id=platform.id, external_key=slug, name="Mismatch Park")
    db_session.add(location)
    db_session.flush()

    participant = Participant(
        platform_id=platform.id,
        external_user_id="999001",
        display_name="Mismatch Runner",
    )
    db_session.add(participant)
    db_session.flush()

    event_date = date(2099, 6, 1)
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"{slug}:{event_date.isoformat()}",
        event_date=event_date,
        event_number=1,
    )
    db_session.add(event)
    db_session.flush()

    db_session.add(
        RunResult(
            participant_id=participant.id,
            event_id=event.id,
            external_result_key=f"profile:999001:{event_date.isoformat()}:{slug}",
            position=10,
            finish_time_sec=1800,
        )
    )
    db_session.flush()

    profile_runs = [
        CanonicalRunResult(
            external_result_key=f"profile:999001:{event_date.isoformat()}:{slug}",
            event_date=event_date,
            external_user_id="999001",
            participant_name="Mismatch Runner",
            position=11,
            finish_time_sec=1800,
            finish_time_display="30:00",
            location_external_key=slug,
            location_name=location.name,
        )
    ]

    mismatches = detect_profile_run_mismatches(
        db_session,
        platform,
        participant,
        profile_runs,
        limit=10,
    )
    assert len(mismatches) == 1
    assert mismatches[0].profile_position == 11
    assert mismatches[0].db_position == 10


def test_detect_profile_run_mismatch_ignores_matching_runs(db_session: Session) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if platform is None:
        pytest.skip("s95 platform not seeded")

    slug = f"match-{uuid4().hex[:8]}"
    location = Location(platform_id=platform.id, external_key=slug, name="Match Park")
    db_session.add(location)
    db_session.flush()

    participant = Participant(
        platform_id=platform.id,
        external_user_id="999002",
        display_name="Match Runner",
    )
    db_session.add(participant)
    db_session.flush()

    event_date = date(2099, 6, 2)
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"{slug}:{event_date.isoformat()}",
        event_date=event_date,
        event_number=2,
    )
    db_session.add(event)
    db_session.flush()

    db_session.add(
        RunResult(
            participant_id=participant.id,
            event_id=event.id,
            external_result_key=f"profile:999002:{event_date.isoformat()}:{slug}",
            position=5,
            finish_time_sec=1500,
        )
    )
    db_session.flush()

    profile_runs = [
        CanonicalRunResult(
            external_result_key=f"profile:999002:{event_date.isoformat()}:{slug}",
            event_date=event_date,
            external_user_id="999002",
            participant_name="Match Runner",
            position=5,
            finish_time_sec=1500,
            finish_time_display="25:00",
            location_external_key=slug,
            location_name=location.name,
        )
    ]

    mismatches = detect_profile_run_mismatches(
        db_session,
        platform,
        participant,
        profile_runs,
        limit=10,
    )
    assert mismatches == []
