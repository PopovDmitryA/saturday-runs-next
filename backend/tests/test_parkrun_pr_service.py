from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Location, Participant, Platform, RunResult
from app.services.personal_record_service import recalculate_personal_records
from app.sync import upsert


@pytest.fixture
def parkrun_platform(db_session: Session) -> Platform:
    return upsert.get_platform(db_session, "parkrun")


@pytest.fixture
def parkrun_location(db_session: Session, parkrun_platform: Platform) -> Location:
    from app.platform_adapters.canonical import CanonicalLocation

    location, _ = upsert.upsert_location(
        db_session,
        parkrun_platform,
        CanonicalLocation(external_key="timiryazevsky", name="Timiryazevsky"),
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
    finish_time_sec: int,
    is_pr: bool = False,
) -> RunResult:
    from app.platform_adapters.canonical import CanonicalEventSummary

    summary, _ = upsert.upsert_event_summary(
        db,
        platform,
        location,
        CanonicalEventSummary(
            external_event_key=f"timiryazevsky:{event_date.isoformat()}",
            event_date=event_date,
            event_number=None,
            location_external_key=location.external_key,
            location_name=location.name,
            source_url=f"https://parkrun.example/{event_date.isoformat()}",
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


def test_recalculate_parkrun_prs_global_best_across_locations(
    db_session: Session,
    parkrun_platform: Platform,
    parkrun_location: Location,
) -> None:
    from app.platform_adapters.canonical import CanonicalLocation

    other_location, _ = upsert.upsert_location(
        db_session,
        parkrun_platform,
        CanonicalLocation(external_key="sokolniki", name="Sokolniki"),
    )
    db_session.flush()

    participant = Participant(
        id=uuid4(),
        platform_id=parkrun_platform.id,
        external_user_id=f"pr-test-{uuid4().hex[:8]}",
        display_name="Test Runner",
    )
    db_session.add(participant)
    db_session.flush()

    first = _add_run(
        db_session,
        platform=parkrun_platform,
        location=parkrun_location,
        participant=participant,
        event_date=date(2022, 1, 1),
        finish_time_sec=1800,
        is_pr=True,
    )
    faster_other_loc = _add_run(
        db_session,
        platform=parkrun_platform,
        location=other_location,
        participant=participant,
        event_date=date(2022, 2, 1),
        finish_time_sec=1700,
        is_pr=False,
    )
    slower_repeat = _add_run(
        db_session,
        platform=parkrun_platform,
        location=parkrun_location,
        participant=participant,
        event_date=date(2022, 3, 1),
        finish_time_sec=1900,
        is_pr=True,
    )

    stats = recalculate_personal_records(db_session, "parkrun", participant_id=participant.id)
    db_session.flush()

    assert stats["pr_runs"] == 2
    assert first.is_pr is True
    assert faster_other_loc.is_pr is True
    assert slower_repeat.is_pr is False


def test_recalculate_parkrun_prs_marks_improvements_only(
    db_session: Session,
    parkrun_platform: Platform,
    parkrun_location: Location,
) -> None:
    participant = Participant(
        id=uuid4(),
        platform_id=parkrun_platform.id,
        external_user_id=f"pr-test-{uuid4().hex[:8]}",
        display_name="Test Runner",
    )
    db_session.add(participant)
    db_session.flush()

    slow = _add_run(
        db_session,
        platform=parkrun_platform,
        location=parkrun_location,
        participant=participant,
        event_date=date(2022, 1, 1),
        finish_time_sec=1800,
        is_pr=True,
    )
    wrong_pr = _add_run(
        db_session,
        platform=parkrun_platform,
        location=parkrun_location,
        participant=participant,
        event_date=date(2022, 2, 1),
        finish_time_sec=1900,
        is_pr=True,
    )
    fast = _add_run(
        db_session,
        platform=parkrun_platform,
        location=parkrun_location,
        participant=participant,
        event_date=date(2022, 3, 1),
        finish_time_sec=1700,
        is_pr=False,
    )

    stats = recalculate_personal_records(db_session, "parkrun", participant_id=participant.id)
    db_session.flush()

    assert stats["pr_runs"] == 2
    assert slow.is_pr is True
    assert wrong_pr.is_pr is False
    assert fast.is_pr is True
