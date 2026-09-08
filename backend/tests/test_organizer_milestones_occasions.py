"""«Юбилеи» и «Скамейка» кабинета: волонтёрства считаются днями, а не событиями.

Пятничная рубрика зовёт поздравить того, кому до юбилея остался один выход.
Пока счёт шёл по событиям, человек с выездами на вторую площадку в ту же
субботу доезжал до юбилея раньше срока: у Владислава КОСТИНА
(5verst.ru/userstats/790224653/) рубрика перед 05.09.2026 обещала 75-е
волонтёрство, а 5verst.ru показывал 72.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, VolunteerResult
from app.services.location_page_service import resolve_location_identity
from app.services.organizer_service import (
    build_location_milestones,
    build_location_volunteer_bench,
)


def _platform(db_session: Session) -> Platform:
    return db_session.query(Platform).filter(Platform.code == "five_verst").one()


def _location(db_session: Session, platform: Platform, tag: str) -> Location:
    location = Location(
        platform_id=platform.id,
        external_key=f"orgocc-{tag}-{uuid4().hex[:8]}",
        name=f"Площадка {tag}",
        city="Москва",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()
    return location


def _volunteering(
    db_session: Session, location: Location, participant: Participant, day: date
) -> Event:
    event = Event(
        platform_id=location.platform_id,
        location_id=location.id,
        external_event_key=f"{location.external_key}:{day.isoformat()}",
        event_date=day,
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(
        VolunteerResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"vol-{uuid4()}",
            role="Сканирование штрих-кодов",
        )
    )
    db_session.flush()
    return event


def test_upcoming_jubilee_counts_days_not_events(db_session: Session) -> None:
    """22 субботы + 2 выезда в те же дни: до 25-го юбилея ещё три, а не один."""
    platform = _platform(db_session)
    home = _location(db_session, platform, "home")
    away = _location(db_session, platform, "away")
    hero = Participant(
        platform_id=platform.id,
        external_user_id=f"orgocc-{uuid4().int % 10**9:09d}",
        display_name="Двухплощадочный Герой",
    )
    db_session.add(hero)
    db_session.flush()

    # Активность должна быть свежей — рубрика смотрит только на действующих.
    last_saturday = date.today() - timedelta(days=date.today().weekday() + 2)
    days = [last_saturday - timedelta(days=7 * i) for i in range(22)]
    for day in days:
        _volunteering(db_session, home, hero, day)
    for day in days[:2]:
        _volunteering(db_session, away, hero, day)
    db_session.commit()

    identity = resolve_location_identity(db_session, home.external_key)
    assert identity is not None
    payload = build_location_milestones(db_session, identity, use_cache=False)
    item = next(
        row
        for row in payload["items"]
        if row["participant_id"] == str(hero.id) and row["kind"] == "vols_platform"
    )
    # 24 события, но 22 дня — до 25-го осталось три выхода, а не один.
    assert item["current"] == 22
    assert item["milestone"] == 25
    assert item["remaining"] == 3

    bench = build_location_volunteer_bench(db_session, identity, use_cache=False)
    row = next(x for x in bench["items"] if x["participant_id"] == str(hero.id))
    assert row["vols_here"] == 22
    assert row["vols_total"] == 22
