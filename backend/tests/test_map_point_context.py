"""Личный контекст точки на карте: прогноз ближайшего старта, «+1 в Нумераторе»
и дальность от дома."""
from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import (
    Event,
    Location,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
)
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.map_point_context_service import build_map_point_context

# Суббота — как и все старты, которые считает сайт.
TODAY = date(2026, 8, 22)
MOSCOW = (55.7500, 37.6200)
NEARBY = (55.8000, 37.7000)


def _user(db_session: Session) -> User:
    user = User(
        telegram_id=int(uuid4().int % 10_000_000_000),
        display_name="Map Point Tester",
        consent_accepted=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _participant(db_session: Session, user: User, platform_code: str) -> Participant:
    platform = db_session.query(Platform).filter(Platform.code == platform_code).one()
    external_user_id = str(uuid4().int % 1_000_000_000)
    participant = Participant(
        platform_id=platform.id,
        external_user_id=external_user_id,
        display_name="Map Point Tester",
        profile_url=f"https://example.test/{external_user_id}/",
    )
    db_session.add(participant)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=external_user_id,
            external_url=participant.profile_url,
        )
    )
    db_session.flush()
    return participant


def _location(
    db_session: Session,
    name: str,
    coordinates: tuple[float, float],
    *,
    platform_code: str = "five_verst",
    is_paused: bool = False,
) -> Location:
    platform = db_session.query(Platform).filter(Platform.code == platform_code).one()
    location = Location(
        platform_id=platform.id,
        external_key=f"mpc-{uuid4().hex[:10]}",
        name=name,
        city=name,
        country="Россия",
        latitude=coordinates[0],
        longitude=coordinates[1],
        is_official_map=True,
        is_paused=is_paused,
    )
    db_session.add(location)
    db_session.flush()
    return location


def _event(
    db_session: Session,
    location: Location,
    *,
    number: int,
    event_date: date,
    runner: Participant | None = None,
) -> Event:
    event = Event(
        platform_id=location.platform_id,
        location_id=location.id,
        external_event_key=f"mpc:{location.external_key}:{number}",
        event_date=event_date,
        event_number=number,
        title=f"MPC #{number}",
    )
    db_session.add(event)
    db_session.flush()
    if runner is not None:
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=runner.id,
                external_result_key=f"{event.external_event_key}:{runner.external_user_id}",
                position=1,
                finish_time_sec=20 * 60,
                finish_time_display="00:20:00",
            )
        )
        db_session.flush()
    return event


def _identity(db_session: Session, location: Location, platform_code: str) -> str:
    return LocationCatalogIndex(db_session).canonical_identity_key(location, platform_code)


def test_next_start_is_last_known_plus_a_week(db_session: Session) -> None:
    user = _user(db_session)
    park = _location(db_session, "MPC Прогноз", MOSCOW)
    _event(db_session, park, number=100, event_date=date(2026, 8, 15))

    context = build_map_point_context(
        db_session, user, _identity(db_session, park, "five_verst"), today=TODAY
    )

    assert len(context["next_starts"]) == 1
    entry = context["next_starts"][0]
    assert entry["number"] == 101
    assert entry["date"] == "2026-08-22"
    assert entry["weeks_ahead"] == 1
    assert entry["platform_code"] == "five_verst"
    assert entry["challenge_code"] == "start_numbers"


def test_skipped_saturdays_roll_the_forecast_forward(db_session: Session) -> None:
    """Пропущенные субботы двигают и дату, и номер: прогноз не должен оставаться
    в прошлом, а `weeks_ahead` показывает, насколько он шаткий."""
    user = _user(db_session)
    park = _location(db_session, "MPC Пропуски", MOSCOW)
    _event(db_session, park, number=50, event_date=date(2026, 8, 1))

    context = build_map_point_context(
        db_session, user, _identity(db_session, park, "five_verst"), today=TODAY
    )

    entry = context["next_starts"][0]
    assert entry["number"] == 53
    assert entry["date"] == "2026-08-22"
    assert entry["weeks_ahead"] == 3


def test_long_silent_location_gets_no_forecast(db_session: Session) -> None:
    """Площадка, молчащая дольше двух месяцев, ещё не помечена паузой — но
    обещать её «ближайший старт» уже нельзя."""
    user = _user(db_session)
    park = _location(db_session, "MPC Молчун", MOSCOW)
    _event(db_session, park, number=10, event_date=date(2026, 5, 2))

    context = build_map_point_context(
        db_session, user, _identity(db_session, park, "five_verst"), today=TODAY
    )

    assert context["next_starts"] == []


def test_paused_location_gets_no_forecast(db_session: Session) -> None:
    user = _user(db_session)
    park = _location(db_session, "MPC Пауза", MOSCOW, is_paused=True)
    _event(db_session, park, number=100, event_date=date(2026, 8, 15))

    context = build_map_point_context(
        db_session, user, _identity(db_session, park, "five_verst"), today=TODAY
    )

    assert context["next_starts"] == []


def test_plus_one_differs_between_overall_and_platform(db_session: Session) -> None:
    """Тот самый случай, ради которого «+1» показывается с двух сторон: номер
    уже взят в другой системе — в сквозном зачёте он ничего не даст, а в зачёте
    этой системы даст."""
    user = _user(db_session)
    park = _location(db_session, "MPC Зачёт", MOSCOW)
    _event(db_session, park, number=100, event_date=date(2026, 8, 15))

    # Номер 101 человек уже брал, но на другой платформе.
    elsewhere = _location(db_session, "MPC Другая система", NEARBY, platform_code="s95")
    _event(
        db_session,
        elsewhere,
        number=101,
        event_date=date(2026, 3, 7),
        runner=_participant(db_session, user, "s95"),
    )

    context = build_map_point_context(
        db_session, user, _identity(db_session, park, "five_verst"), today=TODAY
    )

    entry = context["next_starts"][0]
    assert entry["number"] == 101
    assert entry["plus_one_overall"] is False
    assert entry["plus_one_platform"] is True


def test_anonymous_sees_the_forecast_without_personal_lines(db_session: Session) -> None:
    park = _location(db_session, "MPC Аноним", MOSCOW)
    _event(db_session, park, number=100, event_date=date(2026, 8, 15))

    context = build_map_point_context(
        db_session, None, _identity(db_session, park, "five_verst"), today=TODAY
    )

    assert context["authenticated"] is False
    entry = context["next_starts"][0]
    assert entry["number"] == 101
    assert entry["plus_one_overall"] is None
    assert entry["plus_one_platform"] is None
    assert context["home_distance"] is None


def test_home_distance_counts_from_the_home_location(db_session: Session) -> None:
    user = _user(db_session)
    participant = _participant(db_session, user, "five_verst")
    home = _location(db_session, "MPC Дом", MOSCOW)
    _event(
        db_session,
        home,
        number=40,
        event_date=date(2026, 3, 7),
        runner=participant,
    )
    away = _location(db_session, "MPC Соседний", NEARBY)
    _event(db_session, away, number=100, event_date=date(2026, 8, 15))

    context = build_map_point_context(
        db_session, user, _identity(db_session, away, "five_verst"), today=TODAY
    )

    home_distance = context["home_distance"]
    assert home_distance is not None
    assert home_distance["home_name"] == "MPC Дом"
    assert home_distance["is_home"] is False
    assert home_distance["visited"] is False
    # Между опорными точками ~7 км по прямой — важно, что расстояние вообще
    # посчиталось: без координат площадки плитка показывала бы прочерк.
    assert 5.0 < float(home_distance["distance_km"]) < 10.0


def test_garbage_identity_key_returns_an_empty_context(db_session: Session) -> None:
    context = build_map_point_context(db_session, None, "не-ключ", today=TODAY)
    assert context["next_starts"] == []
