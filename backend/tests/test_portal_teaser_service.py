from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Location, Participant, Platform, PlatformLink, RunResult, User
from app.platform_adapters.canonical import CanonicalEventSummary, CanonicalLocation
from app.services.portal_home_service import _registered_parks
from app.services.portal_teaser_service import build_portal_teaser, normalize_athlete_id
from app.sync import upsert


@pytest.fixture
def five_verst_platform(db_session: Session) -> Platform:
    return upsert.get_platform(db_session, "five_verst")


def _make_location(db: Session, platform: Platform, *, external_key: str, name: str) -> Location:
    location, _ = upsert.upsert_location(db, platform, CanonicalLocation(external_key=external_key, name=name))
    db.flush()
    return location


def _make_participant(db: Session, platform: Platform, *, athlete_id: str) -> Participant:
    participant = Participant(
        id=uuid4(),
        platform_id=platform.id,
        external_user_id=athlete_id,
        display_name="Тизер Тестов",
    )
    db.add(participant)
    db.flush()
    return participant


def _add_run(
    db: Session,
    *,
    platform: Platform,
    location: Location,
    participant: Participant,
    event_date: date,
    finish_time_sec: int | None,
) -> None:
    summary_key = f"{location.external_key}:{event_date.isoformat()}"
    canonical = CanonicalEventSummary(
        external_event_key=summary_key,
        event_date=event_date,
        event_number=None,
        location_external_key=location.external_key,
        location_name=location.name,
        source_url=f"https://example.test/{summary_key}",
        summary_hash=f"hash-{summary_key}",
    )
    summary, _ = upsert.upsert_event_summary(db, platform, location, canonical)
    event = upsert.upsert_event_for_summary(db, platform, location, canonical, summary)
    db.add(
        RunResult(
            id=uuid4(),
            event_id=event.id,
            participant_id=participant.id,
            finish_time_sec=finish_time_sec,
            external_result_key=f"test:{participant.id}:{summary_key}",
        )
    )
    db.flush()


def test_normalize_athlete_id() -> None:
    assert normalize_athlete_id("331") == "331"
    assert normalize_athlete_id("A331") == "331"
    assert normalize_athlete_id("а331") == "331"  # кириллица
    assert normalize_athlete_id(" 12 3 ") == "123"
    assert normalize_athlete_id("abc") is None
    assert normalize_athlete_id("") is None


def test_teaser_aggregates(db_session: Session, five_verst_platform: Platform) -> None:
    suffix = uuid4().hex[:8]
    loc_a = _make_location(db_session, five_verst_platform, external_key=f"a-{suffix}", name="Парк А")
    loc_b = _make_location(db_session, five_verst_platform, external_key=f"b-{suffix}", name="Парк Б")
    athlete_id = str(uuid4().int)[:9]
    participant = _make_participant(db_session, five_verst_platform, athlete_id=athlete_id)

    _add_run(
        db_session,
        platform=five_verst_platform,
        location=loc_a,
        participant=participant,
        event_date=date(2025, 5, 3),
        finish_time_sec=25 * 60,
    )
    _add_run(
        db_session,
        platform=five_verst_platform,
        location=loc_b,
        participant=participant,
        event_date=date(2025, 6, 7),
        finish_time_sec=22 * 60 + 30,
    )
    # Незавершённый результат (без времени) в тизер не считается.
    _add_run(
        db_session,
        platform=five_verst_platform,
        location=loc_b,
        participant=participant,
        event_date=date(2025, 6, 14),
        finish_time_sec=None,
    )

    teaser = build_portal_teaser(db_session, "five_verst", f"A{athlete_id}")
    assert teaser is not None
    assert teaser["finishes"] == 2
    assert teaser["locations"] == 2
    assert teaser["best_time_display"] == "22:30"
    assert teaser["first_event_date"] == date(2025, 5, 3)
    assert teaser["display_name"] == "Тизер Тестов"


def test_teaser_not_found(db_session: Session, five_verst_platform: Platform) -> None:
    assert build_portal_teaser(db_session, "five_verst", "999999999999") is None
    assert build_portal_teaser(db_session, "unknown_platform", "1") is None
    assert build_portal_teaser(db_session, "five_verst", "not-an-id") is None


def test_registered_parks_counts_home_parks(db_session: Session, five_verst_platform: Platform) -> None:
    suffix = uuid4().hex[:8]
    park_one = _make_location(db_session, five_verst_platform, external_key=f"p1-{suffix}", name="Парк 1")
    park_two = _make_location(db_session, five_verst_platform, external_key=f"p2-{suffix}", name="Парк 2")

    baseline = _registered_parks(db_session)

    for index, home in enumerate((park_one, park_two, park_one)):
        user = User(consent_accepted=True, display_name=f"Юзер {index}")
        db_session.add(user)
        db_session.flush()
        participant = _make_participant(db_session, five_verst_platform, athlete_id=str(uuid4().int)[:9])
        db_session.add(
            PlatformLink(
                user_id=user.id,
                platform_id=five_verst_platform.id,
                participant_id=participant.id,
                external_user_id=participant.external_user_id,
                external_url="https://example.test/p",
            )
        )
        db_session.flush()
        # Два финиша в «домашнем» парке и один в другом — дом побеждает.
        other = park_two if home is park_one else park_one
        _add_run(
            db_session,
            platform=five_verst_platform,
            location=home,
            participant=participant,
            event_date=date(2025, 3, 1),
            finish_time_sec=1500,
        )
        _add_run(
            db_session,
            platform=five_verst_platform,
            location=home,
            participant=participant,
            event_date=date(2025, 3, 8),
            finish_time_sec=1500,
        )
        _add_run(
            db_session,
            platform=five_verst_platform,
            location=other,
            participant=participant,
            event_date=date(2025, 3, 15),
            finish_time_sec=1500,
        )

    # Три пользователя, но домашних парка два.
    assert _registered_parks(db_session) == baseline + 2
