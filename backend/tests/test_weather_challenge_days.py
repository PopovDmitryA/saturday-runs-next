"""Сбор «дней на площадке» для погодных челленджей.

Проверяем ровно то, ради чего он заведён (просьба Киры Барановской и решение
Дмитрия 18.09.2026): волонтёрство идёт в зачёт наравне с финишем, но только
такое, ради которого надо было приехать на старт. Пост в канале и обработка
результатов из дома «Моржа» не дают.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import (
    Location,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    StartWeather,
    User,
    VolunteerResult,
)
from app.platform_adapters.canonical import CanonicalEventSummary, CanonicalLocation
from app.services.achievements_service import _collect_run_rows, _collect_weather_days
from app.sync import upsert


@pytest.fixture
def five_verst_platform(db_session: Session) -> Platform:
    return upsert.get_platform(db_session, "five_verst")


def _user_with_participant(db: Session, platform: Platform) -> tuple[User, Participant]:
    user = User(consent_accepted=True, display_name="Погодный волонтёр")
    db.add(user)
    db.flush()
    participant = Participant(
        id=uuid4(),
        platform_id=platform.id,
        external_user_id=f"{platform.code}-{uuid4().hex[:8]}",
        display_name="Погодный волонтёр",
    )
    db.add(participant)
    db.flush()
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
    return user, participant


def _location(db: Session, platform: Platform, *, name: str) -> Location:
    location, _ = upsert.upsert_location(
        db,
        platform,
        CanonicalLocation(external_key=f"loc-{uuid4().hex[:8]}", name=name),
    )
    db.flush()
    return location


def _event(db: Session, platform: Platform, location: Location, when: date):
    key = f"{location.external_key}:{when.isoformat()}"
    summary, _ = upsert.upsert_event_summary(
        db,
        platform,
        location,
        CanonicalEventSummary(
            external_event_key=key,
            event_date=when,
            event_number=None,
            location_external_key=location.external_key,
            location_name=location.name,
            source_url=f"https://example.test/events/{key}",
            summary_hash=f"hash-{key}",
        ),
    )
    event = upsert.upsert_event_for_summary(
        db,
        platform,
        location,
        CanonicalEventSummary(
            external_event_key=key,
            event_date=when,
            event_number=None,
            location_external_key=location.external_key,
            location_name=location.name,
            source_url=summary.source_url or "",
            summary_hash=summary.summary_hash,
        ),
        summary,
    )
    db.flush()
    return event


def _weather(db: Session, location: Location, when: date, *, temperature: float) -> None:
    db.add(
        StartWeather(
            location_id=location.id,
            obs_date=when,
            start_time_local=time(9, 0),
            temperature_c=Decimal(str(temperature)),
            source="era5",
            fetched_at=datetime.now(timezone.utc),
        )
    )
    db.flush()


def test_on_site_volunteering_counts_remote_does_not(
    db_session: Session, five_verst_platform: Platform
) -> None:
    user, participant = _user_with_participant(db_session, five_verst_platform)
    location = _location(db_session, five_verst_platform, name="Морозная")

    ran = date(2026, 1, 3)
    marshalled = date(2026, 1, 10)
    wrote_post = date(2026, 1, 17)
    for when in (ran, marshalled, wrote_post):
        event = _event(db_session, five_verst_platform, location, when)
        _weather(db_session, location, when, temperature=-25.0)
        if when == ran:
            db_session.add(
                RunResult(
                    id=uuid4(),
                    event_id=event.id,
                    participant_id=participant.id,
                    finish_time_sec=1500,
                    finish_time_display="00:25:00",
                    external_result_key=f"run:{participant.id}:{when.isoformat()}",
                )
            )
        else:
            db_session.add(
                VolunteerResult(
                    id=uuid4(),
                    event_id=event.id,
                    participant_id=participant.id,
                    external_result_key=f"vol:{participant.id}:{when.isoformat()}",
                    role="Маршал" if when == marshalled else "Связи с общественностью",
                )
            )
    db_session.flush()

    days = _collect_weather_days(db_session, user.id, _collect_run_rows(db_session, user.id))

    assert [(day.event_date, day.volunteered) for day in days] == [(ran, False), (marshalled, True)]
    # Погода волонтёрскому дню подтянулась — иначе челлендж его не увидит.
    assert days[1].temperature_c == -25.0


def test_run_and_volunteering_on_one_day_count_once(
    db_session: Session, five_verst_platform: Platform
) -> None:
    """Пробежал и отработал в ту же субботу — это один выход на площадку."""
    user, participant = _user_with_participant(db_session, five_verst_platform)
    location = _location(db_session, five_verst_platform, name="Двойная")
    when = date(2026, 2, 7)
    event = _event(db_session, five_verst_platform, location, when)
    _weather(db_session, location, when, temperature=-21.0)
    db_session.add(
        RunResult(
            id=uuid4(),
            event_id=event.id,
            participant_id=participant.id,
            finish_time_sec=1500,
            finish_time_display="00:25:00",
            external_result_key=f"run:{participant.id}:{when.isoformat()}",
        )
    )
    db_session.add(
        VolunteerResult(
            id=uuid4(),
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"vol:{participant.id}:{when.isoformat()}",
            role="Маршал",
        )
    )
    db_session.flush()

    days = _collect_weather_days(db_session, user.id, _collect_run_rows(db_session, user.id))

    assert len(days) == 1
    assert days[0].volunteered is False
