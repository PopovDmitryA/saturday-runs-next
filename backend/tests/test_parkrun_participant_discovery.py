from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Participant, Platform, PlatformLink, RunResult, User, VolunteerResult
from app.platform_adapters.canonical import CanonicalParticipant, CanonicalRunResult
from app.sync.parkrun_participant_discovery import (
    discover_parkrun_participant_from_barcode,
    ensure_parkrun_participant_by_athlete_id,
)


def _mock_parkrun_activity(athlete_id: str):
    profile = CanonicalParticipant(
        external_user_id=athlete_id,
        display_name="Discovered Runner",
        profile_url=f"https://www.parkrun.org.uk/parkrunner/{athlete_id}/",
        total_runs=1,
        total_volunteering=2,
        barcode_id=f"A{athlete_id}",
        source_url=f"https://www.parkrun.org.uk/parkrunner/{athlete_id}/all/",
    )
    runs = [
        CanonicalRunResult(
            external_result_key=f"parkrun:{athlete_id}:hyde-park:2099-01-01:100",
            event_date=date(2099, 1, 1),
            external_user_id=athlete_id,
            participant_name=profile.display_name,
            position=10,
            finish_time_sec=1500,
            finish_time_display="25:00",
            location_external_key="hyde-park",
            location_name="Hyde Park",
            event_number=100,
        )
    ]
    profile_extra = {
        "age_category": "VM35-39",
        "volunteer_summary": [{"role": "Run Director", "occasions": 2}],
        "volunteer_occasions_total": 2,
    }
    return profile, runs, [], profile_extra


def test_discover_skips_ineligible_barcode(db_session: Session) -> None:
    result = discover_parkrun_participant_from_barcode(db_session, "A770005892")
    assert result.attempted is False
    assert result.found is False


def test_discover_skips_when_participant_recent(db_session: Session) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if platform is None:
        pytest.skip("parkrun platform not seeded")

    athlete_id = f"703{uuid4().int % 10000}"
    row = Participant(
        platform_id=platform.id,
        external_user_id=athlete_id,
        display_name="Cached Runner",
        profile_url=f"https://www.parkrun.org.uk/parkrunner/{athlete_id}/",
        barcode_id=f"A{athlete_id}",
        fetched_at=datetime.now(timezone.utc),
    )
    db_session.add(row)
    db_session.flush()

    with patch("app.sync.parkrun_participant_import.parkrun_parser.fetch_athlete_by_id") as fetch_mock:
        result = ensure_parkrun_participant_by_athlete_id(db_session, athlete_id)

    assert result.found is True
    assert result.skipped_recent is True
    assert result.participant_id == str(row.id)
    fetch_mock.assert_not_called()


def test_discover_imports_runs_and_volunteering_without_platform_link(db_session: Session) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if platform is None:
        pytest.skip("parkrun platform not seeded")

    athlete_id = f"704{uuid4().int % 10000}"

    with patch(
        "app.sync.parkrun_participant_import.parkrun_parser.fetch_athlete_by_id",
        return_value=_mock_parkrun_activity(athlete_id),
    ):
        result = discover_parkrun_participant_from_barcode(db_session, f"A{athlete_id}")

    assert result.attempted is True
    assert result.found is True
    assert result.created is True
    assert result.runs_imported == 1
    assert result.volunteering_imported == 1

    participant = (
        db_session.query(Participant)
        .filter(
            Participant.platform_id == platform.id,
            Participant.external_user_id == athlete_id,
        )
        .one()
    )
    assert participant.display_name == "Discovered Runner"
    assert participant.barcode_id == f"A{athlete_id}"
    assert participant.profile_extra.get("volunteer_summary")

    runs = db_session.query(RunResult).filter(RunResult.participant_id == participant.id).all()
    assert len(runs) == 1

    volunteering = (
        db_session.query(VolunteerResult)
        .filter(VolunteerResult.participant_id == participant.id)
        .all()
    )
    assert len(volunteering) == 1
    assert "Run Director" in (volunteering[0].role or "")

    user = User(
        telegram_id=int(uuid4().int % 10_000_000_000),
        display_name="Test User",
    )
    db_session.add(user)
    db_session.flush()
    links = (
        db_session.query(PlatformLink)
        .filter(
            PlatformLink.user_id == user.id,
            PlatformLink.platform_id == platform.id,
        )
        .all()
    )
    assert links == []


def test_discover_from_s95_barcode_normalizes_prefix(db_session: Session) -> None:
    athlete_id = f"705{uuid4().int % 10000}"

    with patch(
        "app.sync.parkrun_participant_import.parkrun_parser.fetch_athlete_by_id",
        return_value=_mock_parkrun_activity(athlete_id),
    ) as fetch_mock:
        result = discover_parkrun_participant_from_barcode(db_session, f"A{athlete_id}")

    assert result.found is True
    fetch_mock.assert_called_once_with(athlete_id)
