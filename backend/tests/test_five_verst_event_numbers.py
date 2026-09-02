"""Номер забега 5 вёрст берётся со страницы локации, а не из волонтёрств профиля.

Волонтёрская таблица профиля 5 вёрст показывает не тот номер, что страница
локации и таблица пробежек: у Дружбы за 15.08.2026 в волонтёрствах «#226», а на
самом деле #228. Мы писали её номер поверх правильного, и журнал протоколов шёл
вразнобой — 220-219-222-221-224-225-224-227-226-229.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform
from app.platform_adapters.canonical import CanonicalVolunteerResult
from app.sync import upsert

EVENT_DATE = date(2026, 8, 15)
TRUE_NUMBER = 228
# Тот самый номер, который 5 вёрст показывают в волонтёрствах за эту же дату.
VOLUNTEER_TABLE_NUMBER = 226


@pytest.fixture
def five_verst_platform(db_session: Session) -> Platform:
    return db_session.query(Platform).filter(Platform.code == "five_verst").one()


@pytest.fixture
def location(db_session: Session, five_verst_platform: Platform) -> Location:
    row = Location(
        platform_id=five_verst_platform.id,
        external_key=f"druzhba-{uuid4().hex[:8]}",
        name="Дружба",
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def participant(db_session: Session, five_verst_platform: Platform) -> Participant:
    # Заведомо не существующий у 5 вёрст диапазон: реальные id начинаются на 79
    # и лежат в dev-БД, случайный из них ловит уникальный индекс.
    external_user_id = f"99{uuid4().int % 10**9:09d}"
    row = Participant(
        platform_id=five_verst_platform.id,
        external_user_id=external_user_id,
        display_name="Волонтёр Тестовый",
        profile_url=f"https://5verst.ru/userstats/{external_user_id}/",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _existing_event(db_session: Session, platform: Platform, location: Location) -> Event:
    """Событие, уже заведённое со страницы локации — с верным номером."""
    row = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"{location.external_key}:{TRUE_NUMBER}:{EVENT_DATE.isoformat()}",
        event_date=EVENT_DATE,
        event_number=TRUE_NUMBER,
        title=f"Дружба #{TRUE_NUMBER}",
        source_url=f"https://5verst.ru/{location.external_key}/results/15.08.2026/",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _volunteering(location: Location, participant: Participant) -> CanonicalVolunteerResult:
    return CanonicalVolunteerResult(
        external_result_key=f"{participant.external_user_id}:{EVENT_DATE.isoformat()}:{location.external_key}",
        event_date=EVENT_DATE,
        external_user_id=participant.external_user_id,
        participant_name="Волонтёр Тестовый",
        role="Организатор",
        source_url=f"https://5verst.ru/{location.external_key}/results/15.08.2026",
        location_external_key=location.external_key,
        location_name="Дружба",
        event_number=VOLUNTEER_TABLE_NUMBER,
    )


def test_volunteering_import_keeps_the_real_number(
    db_session: Session,
    five_verst_platform: Platform,
    location: Location,
    participant: Participant,
) -> None:
    event = _existing_event(db_session, five_verst_platform, location)

    upsert.import_profile_volunteer_results(
        db_session,
        five_verst_platform,
        participant.id,
        [_volunteering(location, participant)],
    )
    db_session.flush()
    db_session.refresh(event)

    assert event.event_number == TRUE_NUMBER
    assert event.title == f"Дружба #{TRUE_NUMBER}"


def test_location_page_number_still_wins_later(
    db_session: Session,
    five_verst_platform: Platform,
    location: Location,
    participant: Participant,
) -> None:
    """Номер со страницы локации по-прежнему записывается — правка не заморозила поле."""
    event = _existing_event(db_session, five_verst_platform, location)

    upsert._apply_event_fields(
        event,
        platform_code="five_verst",
        location=location,
        event_date=EVENT_DATE,
        event_number=TRUE_NUMBER + 2,
        is_test_event=False,
        title=f"Дружба #{TRUE_NUMBER + 2}",
        source_url=event.source_url,
    )
    db_session.flush()

    assert event.event_number == TRUE_NUMBER + 2
