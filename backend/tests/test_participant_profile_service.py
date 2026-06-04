from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, RunResult, User
from app.services.participant_profile_service import (
    resolve_profile_identity,
    try_profile_preview_from_db,
)
from app.services.profile_linking_service import preview_profile_link


def _seed_participant_with_run(db_session: Session, platform_code: str) -> tuple[Platform, Participant, str]:
    platform = db_session.query(Platform).filter(Platform.code == platform_code).one_or_none()
    if platform is None:
        pytest.skip(f"{platform_code} platform not seeded")

    external_user_id = str(uuid4().int % 1_000_000_000)
    if platform_code == "five_verst":
        profile_url = f"https://5verst.ru/userstats/{external_user_id}/"
    elif platform_code == "s95":
        profile_url = f"https://s95.ru/athletes/{external_user_id}/"
    else:
        profile_url = f"https://www.parkrun.org.uk/parkrunner/{external_user_id}/"

    fetched_at = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    participant = Participant(
        platform_id=platform.id,
        external_user_id=external_user_id,
        display_name="Тест УЧАСТНИК",
        profile_url=profile_url,
        fetched_at=fetched_at,
    )
    db_session.add(participant)
    db_session.flush()

    location = Location(
        platform_id=platform.id,
        external_key=f"loc-{external_user_id}",
        name="Тестовая локация",
    )
    db_session.add(location)
    db_session.flush()

    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"evt-{external_user_id}",
        event_date=date(2026, 5, 17),
        title="Тест #1",
    )
    db_session.add(event)
    db_session.flush()

    run = RunResult(
        event_id=event.id,
        participant_id=participant.id,
        external_result_key=f"run-{external_user_id}",
        position=10,
        finish_time_sec=1500,
        finish_time_display="00:25:00",
        fetched_at=fetched_at,
    )
    db_session.add(run)
    db_session.flush()
    return platform, participant, profile_url


def test_try_profile_preview_from_db_returns_freshness(db_session: Session) -> None:
    platform, participant, profile_url = _seed_participant_with_run(db_session, "five_verst")

    preview = try_profile_preview_from_db(db_session, "five_verst", profile_url)
    assert preview is not None
    assert preview.data_source == "database"
    assert preview.external_user_id == participant.external_user_id
    assert preview.display_name == "Тест УЧАСТНИК"
    assert preview.total_runs == 1
    assert preview.data_through_date == date(2026, 5, 17)
    assert preview.data_updated_at == participant.fetched_at
    assert len(preview.recent_activities) == 1


def test_preview_profile_link_uses_db_without_fetch(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _platform, _participant, profile_url = _seed_participant_with_run(db_session, "five_verst")
    user = User(telegram_id=int(uuid4().int % 10_000_000_000), consent_accepted=True)
    db_session.add(user)
    db_session.flush()

    def _fail_fetch(*_args, **_kwargs):
        raise AssertionError("live fetch should not be called when DB has profile")

    monkeypatch.setattr(
        "app.platform_adapters.five_verst.adapter.FiveVerstAdapter.fetch_profile_preview",
        _fail_fetch,
    )

    preview = preview_profile_link(db_session, "five_verst", profile_url, user=user)
    assert preview.data_source == "database"
    assert preview.display_name == "Тест УЧАСТНИК"


def test_resolve_profile_identity_accepts_five_verst_code() -> None:
    identity = resolve_profile_identity("five_verst", "790103773")
    assert identity.external_user_id == "790103773"
    assert identity.canonical_profile_url.endswith("/790103773/")
