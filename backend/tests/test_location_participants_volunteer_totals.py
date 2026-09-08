"""Постоянный состав: колонка «Всего» у волонтёров считает дни, а не события.

5 вёрст засчитывают волонтёрство раз в календарный день, и так же считают
личная страница, рейтинги и кабинет (app.volunteering_occasions). Страница
состава раньше складывала СОБЫТИЯ — и у тех, кто в одну субботу успевает на
две площадки, «Всего» уезжало вверх: Владислав КОСТИН
(5verst.ru/userstats/790224653/) выходил так трижды и получал 75 против 72 на
самом 5verst.ru.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, VolunteerResult
from app.services.location_page_service import _active_participant_rows


def _platform(db_session: Session, code: str) -> Platform:
    return db_session.query(Platform).filter(Platform.code == code).one()


def _location(db_session: Session, name: str) -> Location:
    row = Location(
        platform_id=_platform(db_session, "five_verst").id,
        external_key=f"voltotals-{uuid4().hex[:8]}",
        name=name,
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def home(db_session: Session) -> Location:
    return _location(db_session, "Домашняя площадка")


@pytest.fixture
def away(db_session: Session) -> Location:
    return _location(db_session, "Соседняя площадка")


@pytest.fixture
def participant(db_session: Session) -> Participant:
    row = Participant(
        platform_id=_platform(db_session, "five_verst").id,
        external_user_id=f"97{uuid4().int % 10**9:09d}",
        display_name="Владислав Двухплощадочный",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _volunteering(
    db_session: Session,
    location: Location,
    participant: Participant,
    event_date: date,
    *,
    is_test_event: bool = False,
) -> Event:
    event = Event(
        platform_id=location.platform_id,
        location_id=location.id,
        external_event_key=f"{location.external_key}:{event_date.isoformat()}",
        event_date=event_date,
        is_test_event=is_test_event,
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(
        VolunteerResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"{event.external_event_key}:{participant.external_user_id}",
            role="Сканирование штрих-кодов",
        )
    )
    db_session.flush()
    return event


def _row(db_session: Session, events: list[Event], name: str) -> dict[str, object]:
    rows, _total = _active_participant_rows(
        db_session, VolunteerResult, [event.id for event in events], test_event_ids=[]
    )
    return next(item for item in rows if item["name"] == name)


def test_two_locations_in_one_day_count_once(
    db_session: Session, home: Location, away: Location, participant: Participant
) -> None:
    """Суббота на двух площадках — одно волонтёрство, как на 5 вёрст."""
    days = [date(2026, 3, 7), date(2026, 3, 14), date(2026, 3, 21)]
    home_events = [_volunteering(db_session, home, participant, day) for day in days]
    _volunteering(db_session, away, participant, days[-1])

    row = _row(db_session, home_events, "Владислав Двухплощадочный")
    assert row["count"] == 3
    assert row["total_count"] == 3


def test_test_event_stays_out_of_total(
    db_session: Session, home: Location, away: Location, participant: Participant
) -> None:
    """Тестовый (пробный) старт площадки в «Всего» не идёт."""
    days = [date(2026, 3, 7), date(2026, 3, 14), date(2026, 3, 21)]
    home_events = [_volunteering(db_session, home, participant, day) for day in days]
    _volunteering(db_session, away, participant, date(2026, 2, 28), is_test_event=True)

    row = _row(db_session, home_events, "Владислав Двухплощадочный")
    assert row["total_count"] == 3


def test_inventory_day_counts_per_location(
    db_session: Session, home: Location, away: Location, participant: Participant
) -> None:
    """1 января — инвентаризация: зачёт на каждую разную локацию этого дня."""
    days = [date(2026, 1, 1), date(2026, 3, 7), date(2026, 3, 14)]
    home_events = [_volunteering(db_session, home, participant, day) for day in days]
    _volunteering(db_session, away, participant, date(2026, 1, 1))

    row = _row(db_session, home_events, "Владислав Двухплощадочный")
    assert row["count"] == 3
    assert row["total_count"] == 4
