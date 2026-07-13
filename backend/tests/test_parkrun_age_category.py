from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Platform
from app.parkrun.age_category import normalize_parkrun_age_group
from app.platform_adapters.canonical import CanonicalParticipant
from app.sync.parkrun_participant_import import apply_parkrun_participant


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("SM25-29", "SM25-29"),
        (" VM40-44 ", "VM40-44"),
        ("JM10", "JM10"),
        ("SW18-19", "SW18-19"),
        ("VW70-74", "VW70-74"),
        ("50.59%", None),
        ("54.55 %", None),
        ("", None),
        ("---", None),
        (None, None),
        (42, None),
    ],
)
def test_normalize_parkrun_age_group(value, expected) -> None:
    assert normalize_parkrun_age_group(value) == expected


def _profile(athlete_id: str) -> CanonicalParticipant:
    return CanonicalParticipant(
        external_user_id=athlete_id,
        display_name="Test Runner",
        profile_url=f"https://www.parkrun.org.uk/parkrunner/{athlete_id}/",
        total_runs=5,
        barcode_id=f"A{athlete_id}",
        source_url=f"https://www.parkrun.org.uk/parkrunner/{athlete_id}/all/",
    )


def _get_parkrun_platform(db: Session) -> Platform:
    platform = db.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if platform is None:
        pytest.skip("parkrun platform not seeded")
    return platform


def test_apply_parkrun_participant_writes_age_and_checked_at(db_session: Session) -> None:
    platform = _get_parkrun_platform(db_session)
    athlete_id = f"705{uuid4().int % 100000}"

    row, created = apply_parkrun_participant(
        db_session,
        platform,
        _profile(athlete_id),
        profile_extra={"age_category": "SM25-29"},
    )
    assert created is True
    assert row.age_category == "SM25-29"
    assert row.profile_checked_at is not None


def test_apply_parkrun_participant_keeps_existing_age_when_page_has_none(db_session: Session) -> None:
    platform = _get_parkrun_platform(db_session)
    athlete_id = f"706{uuid4().int % 100000}"

    apply_parkrun_participant(
        db_session,
        platform,
        _profile(athlete_id),
        profile_extra={"age_category": "VM35-39"},
    )
    row, created = apply_parkrun_participant(
        db_session,
        platform,
        _profile(athlete_id),
        profile_extra={"age_category": None},
    )
    assert created is False
    assert row.age_category == "VM35-39"


def test_apply_parkrun_participant_rejects_age_grade_percent(db_session: Session) -> None:
    platform = _get_parkrun_platform(db_session)
    athlete_id = f"707{uuid4().int % 100000}"

    row, _ = apply_parkrun_participant(
        db_session,
        platform,
        _profile(athlete_id),
        profile_extra={"age_category": "50.59%"},
    )
    assert row.age_category is None


def test_run_import_does_not_overwrite_age_group_with_age_grade(db_session: Session) -> None:
    """Регрессия: до 09.06.2026 импорт результатов затирал возрастную группу
    участника age grade'ом забега («50.59%») — так испортились ~54k строк."""
    from datetime import date
    from unittest.mock import patch

    from app.platform_adapters.canonical import CanonicalRunResult
    from app.sync.parkrun_participant_import import import_parkrun_participant_activity

    platform = _get_parkrun_platform(db_session)
    athlete_id = f"708{uuid4().int % 100000}"
    runs = [
        CanonicalRunResult(
            external_result_key=f"parkrun:{athlete_id}:hyde-park:2024-01-06:100",
            event_date=date(2024, 1, 6),
            external_user_id=athlete_id,
            participant_name="Test Runner",
            position=13,
            finish_time_sec=1530,
            finish_time_display="25:30",
            age_category="50.59%",
            location_external_key="hyde-park",
            location_name="Hyde Park",
            event_number=100,
        )
    ]
    profile_extra = {"age_category": "SM25-29", "volunteer_summary": [], "volunteer_occasions_total": 0}

    with patch(
        "app.sync.parkrun_participant_import.parkrun_parser.fetch_athlete_by_id",
        return_value=(_profile(athlete_id), runs, [], profile_extra),
    ):
        imported = import_parkrun_participant_activity(db_session, platform, athlete_id)

    assert imported.runs_imported == 1
    assert imported.participant.age_category == "SM25-29"
    assert imported.participant.profile_checked_at is not None
