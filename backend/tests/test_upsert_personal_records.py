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
                position=slow.position,
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

    # Дебют — baseline (стоявший is_pr=True снимается), рекорд — только улучшение.
    assert slow.is_pr is False
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

    # У A дебют перестаёт быть рекордом, а B пересчёт не трогает вовсе.
    assert run_a.is_pr is False
    assert run_b.is_pr is True


@pytest.fixture
def five_verst_platform(db_session: Session) -> Platform:
    return upsert.get_platform(db_session, "five_verst")


@pytest.fixture
def five_verst_location(db_session: Session, five_verst_platform: Platform) -> Location:
    from app.platform_adapters.canonical import CanonicalLocation

    location, _ = upsert.upsert_location(
        db_session,
        five_verst_platform,
        CanonicalLocation(external_key="natashinsky", name="Наташинский"),
    )
    db_session.flush()
    return location


def _add_five_verst_run(
    db: Session,
    *,
    platform: Platform,
    location: Location,
    participant: Participant,
    event_date: date,
    finish_time_sec: int | None,
    achievement_labels: list[str] | None = None,
    is_first_run: bool = False,
) -> RunResult:
    from app.platform_adapters.canonical import CanonicalEventSummary

    summary, _ = upsert.upsert_event_summary(
        db,
        platform,
        location,
        CanonicalEventSummary(
            external_event_key=f"natashinsky:{event_date.isoformat()}",
            event_date=event_date,
            event_number=None,
            location_external_key=location.external_key,
            location_name=location.name,
            source_url=f"https://5verst.ru/events/natashinsky/{event_date.isoformat()}",
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
        finish_time_display=(
            f"00:{finish_time_sec // 60:02d}:{finish_time_sec % 60:02d}" if finish_time_sec is not None else None
        ),
        is_pr=False,
        is_first_run=is_first_run,
        achievement_labels=achievement_labels or [],
        external_result_key=f"test:{participant.id}:{event_date.isoformat()}",
    )
    db.add(run)
    db.flush()
    return run


def test_recalculate_five_verst_preserves_protocol_personal_record_labels(
    db_session: Session,
    five_verst_platform: Platform,
    five_verst_location: Location,
) -> None:
    participant = Participant(
        id=uuid4(),
        platform_id=five_verst_platform.id,
        external_user_id=f"5v-pr-{uuid4().hex[:8]}",
        display_name="5verst Runner",
    )
    db_session.add(participant)
    db_session.flush()

    first = _add_five_verst_run(
        db_session,
        platform=five_verst_platform,
        location=five_verst_location,
        participant=participant,
        event_date=date(2022, 5, 21),
        finish_time_sec=1256,
    )
    faster = _add_five_verst_run(
        db_session,
        platform=five_verst_platform,
        location=five_verst_location,
        participant=participant,
        event_date=date(2022, 6, 4),
        finish_time_sec=1161,
    )
    slower_with_label = _add_five_verst_run(
        db_session,
        platform=five_verst_platform,
        location=five_verst_location,
        participant=participant,
        event_date=date(2022, 6, 18),
        finish_time_sec=1183,
        achievement_labels=["Личный рекорд!", "Первый финиш на Раменское Городской парк"],
    )

    stats = recalculate_personal_records(db_session, "five_verst", participant_id=participant.id)
    db_session.flush()

    # first — дебют (baseline), faster — рекорд по времени, slower_with_label —
    # медленнее лучшего, но метка протокола 5 вёрст авторитетна и сохраняется.
    assert stats["pr_runs"] == 2
    assert first.is_pr is False
    assert faster.is_pr is True
    assert slower_with_label.is_pr is True


def test_recalculate_five_verst_debut_and_no_time_runs_are_not_pr(
    db_session: Session,
    five_verst_platform: Platform,
    five_verst_location: Location,
) -> None:
    participant = Participant(
        id=uuid4(),
        platform_id=five_verst_platform.id,
        external_user_id=f"5v-first-{uuid4().hex[:8]}",
        display_name="5verst First Timer",
    )
    db_session.add(participant)
    db_session.flush()

    earlier_without_time = _add_five_verst_run(
        db_session,
        platform=five_verst_platform,
        location=five_verst_location,
        participant=participant,
        event_date=date(2022, 5, 7),
        finish_time_sec=None,
    )
    first_finisher = _add_five_verst_run(
        db_session,
        platform=five_verst_platform,
        location=five_verst_location,
        participant=participant,
        event_date=date(2022, 6, 11),
        finish_time_sec=1424,
        achievement_labels=["Первый финиш на 5 вёрст"],
        is_first_run=True,
    )
    slower_repeat = _add_five_verst_run(
        db_session,
        platform=five_verst_platform,
        location=five_verst_location,
        participant=participant,
        event_date=date(2022, 6, 25),
        finish_time_sec=1500,
    )

    stats = recalculate_personal_records(db_session, "five_verst", participant_id=participant.id)
    db_session.flush()

    # Забег без времени и первый результат с временем (baseline) — не рекорды;
    # метка «Первый финиш на 5 вёрст» — дебют, а не «личный рекорд».
    assert stats["pr_runs"] == 0
    assert earlier_without_time.is_pr is False
    assert first_finisher.is_pr is False
    assert slower_repeat.is_pr is False


def test_recalculate_first_runs_with_and_without_time_are_not_pr(
    db_session: Session,
    s95_platform: Platform,
    s95_location: Location,
) -> None:
    participant = Participant(
        id=uuid4(),
        platform_id=s95_platform.id,
        external_user_id=f"s95-first-{uuid4().hex[:8]}",
        display_name="S95 First Timer",
    )
    db_session.add(participant)
    db_session.flush()

    first = _add_s95_run(
        db_session,
        platform=s95_platform,
        location=s95_location,
        participant=participant,
        event_date=date(2022, 1, 1),
        finish_time_sec=0,
        is_pr=False,
    )
    first.finish_time_sec = None
    first.finish_time_display = None
    second = _add_s95_run(
        db_session,
        platform=s95_platform,
        location=s95_location,
        participant=participant,
        event_date=date(2022, 2, 1),
        finish_time_sec=1700,
        is_pr=False,
    )

    stats = recalculate_personal_records(db_session, "s95", participant_id=participant.id)
    db_session.flush()

    # Первый забег без времени — не рекорд; второй — первый результат с временем,
    # т.е. baseline, тоже не рекорд.
    assert stats["pr_runs"] == 0
    assert first.is_pr is False
    assert second.is_pr is False


def test_protocol_upsert_does_not_mark_debut_as_pr_for_new_participant(
    db_session: Session,
    five_verst_platform: Platform,
    five_verst_location: Location,
) -> None:
    from app.models import Event
    from app.platform_adapters.canonical import CanonicalEventSummary, CanonicalRunResult

    external_user_id = f"5v-new-{uuid4().hex[:8]}"
    participant = Participant(
        id=uuid4(),
        platform_id=five_verst_platform.id,
        external_user_id=external_user_id,
        display_name="New 5verst Runner",
    )
    db_session.add(participant)
    db_session.flush()

    summary, _ = upsert.upsert_event_summary(
        db_session,
        five_verst_platform,
        five_verst_location,
        CanonicalEventSummary(
            external_event_key="natashinsky:2024-06-01",
            event_date=date(2024, 6, 1),
            event_number=None,
            location_external_key=five_verst_location.external_key,
            location_name=five_verst_location.name,
            source_url="https://5verst.ru/events/natashinsky/2024-06-01",
            summary_hash="hash-new-runner",
        ),
    )
    event = upsert.upsert_event_for_summary(
        db_session,
        five_verst_platform,
        five_verst_location,
        CanonicalEventSummary(
            external_event_key=summary.external_event_key,
            event_date=date(2024, 6, 1),
            event_number=None,
            location_external_key=five_verst_location.external_key,
            location_name=five_verst_location.name,
            source_url=summary.source_url or "",
            summary_hash=summary.summary_hash,
        ),
        summary,
    )
    upsert.upsert_run_results(
        db_session,
        event,
        five_verst_platform,
        [
            CanonicalRunResult(
                external_result_key=f"protocol:{external_user_id}:2024-06-01",
                event_date=date(2024, 6, 1),
                external_user_id=external_user_id,
                participant_name=participant.display_name,
                position=42,
                finish_time_sec=1500,
                finish_time_display="00:25:00",
                is_pr=False,
                location_external_key=five_verst_location.external_key,
                location_name=five_verst_location.name,
            )
        ],
        from_profile=False,
    )
    db_session.flush()

    run = (
        db_session.query(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .filter(
            Event.id == event.id,
            RunResult.participant_id == participant.id,
        )
        .one()
    )
    # Единственный (первый в нашей БД) забег — baseline, не рекорд.
    assert run.is_pr is False


def test_recalculate_skips_secondary_crosslink_duplicate(
    db_session: Session,
    s95_platform: Platform,
    s95_location: Location,
) -> None:
    """A duplicate run (secondary in event_crosslinks) must not carry is_pr, even if
    its finish time is the fastest — the PR stays on the counted run."""
    from app.models import EventCrosslink

    participant = Participant(
        id=uuid4(),
        platform_id=s95_platform.id,
        external_user_id=f"s95-dup-{uuid4().hex[:8]}",
        display_name="Dup Runner",
    )
    db_session.add(participant)
    db_session.flush()

    counted = _add_s95_run(
        db_session,
        platform=s95_platform,
        location=s95_location,
        participant=participant,
        event_date=date(2022, 1, 1),
        finish_time_sec=1800,
        is_pr=True,
    )
    duplicate = _add_s95_run(
        db_session,
        platform=s95_platform,
        location=s95_location,
        participant=participant,
        event_date=date(2022, 2, 1),
        finish_time_sec=1700,  # faster, but it's a "не в зачёте" duplicate
        is_pr=True,
    )
    improvement = _add_s95_run(
        db_session,
        platform=s95_platform,
        location=s95_location,
        participant=participant,
        event_date=date(2022, 3, 1),
        finish_time_sec=1750,  # faster than counted 1800, slower than the excluded 1700
        is_pr=False,
    )
    # Mark the faster run's event as a secondary crosslink duplicate.
    db_session.add(
        EventCrosslink(primary_event_id=counted.event_id, secondary_event_id=duplicate.event_id)
    )
    db_session.flush()

    recalculate_personal_records(db_session, "s95", participant_id=participant.id)
    db_session.flush()

    assert duplicate.is_pr is False  # duplicate never a PR, despite the best time
    assert counted.is_pr is False    # debut is a baseline, not a PR
    # 1750 — PR относительно зачётного лучшего (1800): исключённый дубль 1700
    # не участвует в baseline.
    assert improvement.is_pr is True
