"""Tests for is_pr recalculation after run upserts."""
from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Location, Participant, Platform, RunResult
from app.platform_adapters.canonical import CanonicalRunResult
from app.services.personal_record_service import recalculate_personal_records
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


def _add_s95_run(
    db: Session,
    *,
    platform: Platform,
    location: Location,
    participant: Participant,
    event_date: date,
    finish_time_sec: int,
    is_pr: bool,
) -> RunResult:
    from app.platform_adapters.canonical import CanonicalEventSummary

    summary, _ = upsert.upsert_event_summary(
        db,
        platform,
        location,
        CanonicalEventSummary(
            external_event_key=f"zil:{event_date.isoformat()}",
            event_date=event_date,
            event_number=None,
            location_external_key=location.external_key,
            location_name=location.name,
            source_url=f"https://s95.ru/events/zil/{event_date.isoformat()}",
            summary_hash=f"hash-{event_date.isoformat()}",
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
        is_pr=is_pr,
        external_result_key=f"test:{participant.id}:{event_date.isoformat()}",
    )
    db.add(run)
    db.flush()
    return run


def test_protocol_upsert_recalculates_s95_pr_from_finish_times(
    db_session: Session,
    s95_platform: Platform,
    s95_location: Location,
) -> None:
    participant = Participant(
        id=uuid4(),
        platform_id=s95_platform.id,
        external_user_id=f"s95-pr-{uuid4().hex[:8]}",
        display_name="S95 Runner",
    )
    db_session.add(participant)
    db_session.flush()

    slow = _add_s95_run(
        db_session,
        platform=s95_platform,
        location=s95_location,
        participant=participant,
        event_date=date(2022, 1, 1),
        finish_time_sec=1800,
        is_pr=True,
    )
    fast = _add_s95_run(
        db_session,
        platform=s95_platform,
        location=s95_location,
        participant=participant,
        event_date=date(2022, 2, 1),
        finish_time_sec=1700,
        is_pr=True,
    )

    from app.models import Event

    event = db_session.get(Event, slow.event_id)
    assert event is not None
    upsert.upsert_run_results(
        db_session,
        event,
        s95_platform,
        [
            CanonicalRunResult(
                external_result_key=slow.external_result_key,
                event_date=date(2022, 1, 1),
                external_user_id=participant.external_user_id,
                participant_name=participant.display_name,
                finish_time_sec=1800,
                finish_time_display="00:30:00",
                is_pr=False,
                location_external_key="zil",
                location_name="ЗИЛ",
            )
        ],
        from_profile=False,
    )
    db_session.flush()

    assert slow.is_pr is True
    assert fast.is_pr is True


def test_recalculate_s95_single_participant_does_not_reset_others(
    db_session: Session,
    s95_platform: Platform,
    s95_location: Location,
) -> None:
    participant_a = Participant(
        id=uuid4(),
        platform_id=s95_platform.id,
        external_user_id=f"s95-a-{uuid4().hex[:8]}",
        display_name="Runner A",
    )
    participant_b = Participant(
        id=uuid4(),
        platform_id=s95_platform.id,
        external_user_id=f"s95-b-{uuid4().hex[:8]}",
        display_name="Runner B",
    )
    db_session.add_all([participant_a, participant_b])
    db_session.flush()

    run_a = _add_s95_run(
        db_session,
        platform=s95_platform,
        location=s95_location,
        participant=participant_a,
        event_date=date(2022, 1, 1),
        finish_time_sec=1800,
        is_pr=True,
    )
    run_b = _add_s95_run(
        db_session,
        platform=s95_platform,
        location=s95_location,
        participant=participant_b,
        event_date=date(2022, 1, 1),
        finish_time_sec=1700,
        is_pr=True,
    )

    recalculate_personal_records(db_session, "s95", participant_id=participant_a.id)
    db_session.flush()

    assert run_a.is_pr is True
    assert run_b.is_pr is True
