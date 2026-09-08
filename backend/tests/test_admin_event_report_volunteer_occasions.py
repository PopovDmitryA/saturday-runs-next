"""Номер волонтёрства в отчёте и своде — по дням, а не по событиям.

5 вёрст засчитывают волонтёрство раз в календарный день, и так же считает весь
сайт (app.volunteering_occasions). Отчёт же складывал СОБЫТИЯ — и тот, кто в
одну субботу успевает на две площадки, получал лишние номера. У Владислава
КОСТИНА (5verst.ru/userstats/790224653/) таких дней три: на 05.09.2026 отчёт
насчитывал ему 75-е волонтёрство в системе и звал в пост «юбилей», хотя сам
5verst.ru показывал 72.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, VolunteerResult
from app.services.admin_event_report_service import build_event_report, build_event_svod


def _platform(db_session: Session) -> Platform:
    return db_session.query(Platform).filter(Platform.code == "five_verst").one()


def _location(db_session: Session, platform: Platform, tag: str) -> Location:
    location = Location(
        platform_id=platform.id,
        external_key=f"occ-{tag}-{uuid4().hex[:8]}",
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
        title="Старт",
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


def _participant(db_session: Session, platform: Platform, name: str) -> Participant:
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"occ-{uuid4().int % 10**9:09d}",
        display_name=name,
        profile_url="https://example.test/occ/",
    )
    db_session.add(participant)
    db_session.flush()
    return participant


def test_two_locations_in_one_day_do_not_inflate_jubilee(db_session: Session) -> None:
    """22 субботы + 2 выезда на вторую площадку в те же дни = 23-е волонтёрство.

    По событиям вышло бы 25 — ровно клубный порог, и человек уехал бы в пост
    «юбилейным» на два выхода раньше срока.
    """
    platform = _platform(db_session)
    home = _location(db_session, platform, "home")
    away = _location(db_session, platform, "away")
    hero = _participant(db_session, platform, "Двухплощадочный Герой")

    first = date(2024, 2, 3)
    past_days = [first + timedelta(days=7 * i) for i in range(22)]
    for day in past_days:
        _volunteering(db_session, home, hero, day)
    for day in past_days[:2]:
        _volunteering(db_session, away, hero, day)

    today = _volunteering(db_session, home, hero, past_days[-1] + timedelta(days=7))
    db_session.commit()

    svod = build_event_svod(db_session, today.id)
    assert svod is not None
    row = next(item for item in svod["volunteers"] if item["name"] == "Двухплощадочный Герой")
    assert row["platform_vol_count"] == 23
    assert row["platform_milestone"] is None
    # На домашней площадке выездов не было — счёт локации не меняется.
    assert row["location_vol_count"] == 23

    report = build_event_report(db_session, today.id)
    assert report is not None
    assert [item["count"] for item in report["clubs"]["volunteering"]] == []


def test_inventory_day_counts_each_location(db_session: Session) -> None:
    """1 января — инвентаризация: две площадки в этот день дают два зачёта."""
    platform = _platform(db_session)
    home = _location(db_session, platform, "home")
    away = _location(db_session, platform, "away")
    hero = _participant(db_session, platform, "Инвентаризатор")

    _volunteering(db_session, away, hero, date(2025, 1, 1))
    today = _volunteering(db_session, home, hero, date(2025, 1, 1))
    db_session.commit()

    svod = build_event_svod(db_session, today.id)
    assert svod is not None
    row = next(item for item in svod["volunteers"] if item["name"] == "Инвентаризатор")
    assert row["platform_vol_count"] == 2


def test_ordinary_history_keeps_plain_numbering(db_session: Session) -> None:
    """Без выездов номер прежний: десятая суббота — это десятое волонтёрство."""
    platform = _platform(db_session)
    home = _location(db_session, platform, "home")
    hero = _participant(db_session, platform, "Домосед")

    first = date(2024, 3, 2)
    for i in range(9):
        _volunteering(db_session, home, hero, first + timedelta(days=7 * i))
    today = _volunteering(db_session, home, hero, first + timedelta(days=7 * 9))
    db_session.commit()

    svod = build_event_svod(db_session, today.id)
    assert svod is not None
    row = next(item for item in svod["volunteers"] if item["name"] == "Домосед")
    assert row["platform_vol_count"] == 10
    assert row["platform_milestone"] == 10
    assert row["location_vol_count"] == 10
    assert row["location_milestone"] == 10

    report = build_event_report(db_session, today.id)
    assert report is not None
    assert [item["count"] for item in report["clubs"]["volunteering"]] == [10]
