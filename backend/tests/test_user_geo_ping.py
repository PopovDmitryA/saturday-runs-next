"""Огрублённая отметка положения: округление, одна строка в сутки, отсев мусора."""
from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Location, Platform, User, UserGeoPing
from app.services.user_geo_ping_service import MAX_ACCURACY_M, record_geo_ping

TODAY = date(2026, 8, 22)
# Точка в Москве с «лишними» знаками — на них и проверяем огрубление.
MOSCOW_PRECISE = (55.751244, 37.618423)


def _user(db_session: Session) -> User:
    user = User(
        telegram_id=int(uuid4().int % 10_000_000_000),
        display_name="Geo Ping Tester",
        consent_accepted=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _location(db_session: Session, name: str, coordinates: tuple[float, float]) -> Location:
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    location = Location(
        platform_id=platform.id,
        external_key=f"geoping-{uuid4().hex[:10]}",
        name=name,
        city=name,
        country="Россия",
        latitude=coordinates[0],
        longitude=coordinates[1],
        is_official_map=True,
    )
    db_session.add(location)
    db_session.flush()
    return location


def _pings(db_session: Session, user: User) -> list[UserGeoPing]:
    return db_session.query(UserGeoPing).filter(UserGeoPing.user_id == user.id).all()


def test_coordinates_are_stored_rounded(db_session: Session) -> None:
    """В базу не должны попадать точные координаты: два знака — клетка примерно
    километр на километр, дом и работа на ней уже неразличимы."""
    user = _user(db_session)

    assert record_geo_ping(
        db_session,
        user,
        latitude=MOSCOW_PRECISE[0],
        longitude=MOSCOW_PRECISE[1],
        accuracy_m=25,
        today=TODAY,
    )

    ping = _pings(db_session, user)[0]
    assert ping.latitude == 55.75
    assert ping.longitude == 37.62
    assert ping.observed_on == TODAY
    assert ping.accuracy_m == 25


def test_only_one_ping_per_day(db_session: Session) -> None:
    user = _user(db_session)
    assert record_geo_ping(
        db_session, user, latitude=55.75, longitude=37.62, today=TODAY
    )
    # Вторая попытка в тот же день — молча мимо, первая отметка остаётся.
    assert not record_geo_ping(
        db_session, user, latitude=59.94, longitude=30.31, today=TODAY
    )

    pings = _pings(db_session, user)
    assert len(pings) == 1
    assert pings[0].latitude == 55.75


def test_next_day_gets_its_own_ping(db_session: Session) -> None:
    user = _user(db_session)
    record_geo_ping(db_session, user, latitude=55.75, longitude=37.62, today=TODAY)
    assert record_geo_ping(
        db_session, user, latitude=59.94, longitude=30.31, today=date(2026, 8, 23)
    )
    assert len(_pings(db_session, user)) == 2


def test_rough_fix_is_dropped(db_session: Session) -> None:
    """Определение по вышкам с разбросом в десятки километров не говорит даже
    про город — такие отметки не пишем вовсе."""
    user = _user(db_session)
    assert not record_geo_ping(
        db_session,
        user,
        latitude=55.75,
        longitude=37.62,
        accuracy_m=MAX_ACCURACY_M + 1,
        today=TODAY,
    )
    assert _pings(db_session, user) == []


def test_impossible_coordinates_are_dropped(db_session: Session) -> None:
    user = _user(db_session)
    assert not record_geo_ping(
        db_session, user, latitude=155.0, longitude=37.62, today=TODAY
    )
    assert _pings(db_session, user) == []


def test_nearest_location_is_recorded(db_session: Session) -> None:
    """Ради этих двух чисел всё и собирается: по ним видно города, где участники
    есть, а площадки поблизости нет."""
    user = _user(db_session)
    near = _location(db_session, "Geo Ping Рядом", (55.80, 37.70))
    db_session.commit()

    record_geo_ping(db_session, user, latitude=55.75, longitude=37.62, today=TODAY)

    ping = _pings(db_session, user)[0]
    assert ping.nearest_identity_key is not None
    assert ping.nearest_distance_km is not None
    # Ближайшей может оказаться и другая площадка из общей базы — проверяем, что
    # расстояние вообще посчиталось и выглядит правдоподобно.
    assert 0 <= ping.nearest_distance_km <= 100
    assert near.name == "Geo Ping Рядом"


def test_user_without_runs_has_no_home(db_session: Session) -> None:
    """Дома нет — отметка всё равно пишется: город без домашней локации тоже
    интересен, а поля дома просто пустые."""
    user = _user(db_session)
    assert record_geo_ping(
        db_session, user, latitude=55.75, longitude=37.62, today=TODAY
    )
    ping = _pings(db_session, user)[0]
    assert ping.home_identity_key is None
    assert ping.home_distance_km is None
