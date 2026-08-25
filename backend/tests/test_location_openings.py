"""Открытия локаций: разметка «какой старт первый» и рейтинг по ней."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models import (
    Event,
    EventCrosslink,
    Location,
    LocationCatalog,
    LocationCatalogLink,
    LocationOpening,
    Participant,
    Platform,
    RunResult,
    User,
)
from app.services.leaderboard_service import _my_opening_values
from app.services.location_openings_service import (
    AUTO_OPENING_PLATFORMS,
    MANUAL_OPENING_PLATFORMS,
    clear_opening,
    list_openings,
    resolve_opening_number,
    set_opening,
)


def test_resolve_opening_number_defaults_per_platform() -> None:
    # 5 вёрст / parkrun / RunPark: открытие видно из протокола.
    for code in AUTO_OPENING_PLATFORMS:
        assert resolve_opening_number(code, None) == 1
    # С95 без ручной разметки открытий не даёт вовсе.
    for code in MANUAL_OPENING_PLATFORMS:
        assert resolve_opening_number(code, None) is None


def test_resolve_opening_number_manual_wins_over_protocol() -> None:
    override = LocationOpening(location_id=uuid4(), opening_event_number=7)
    assert resolve_opening_number("five_verst", override) == 7
    # Пустой номер у сохранённой строки — «открытия нет», а не «не знаю».
    silenced = LocationOpening(location_id=uuid4(), opening_event_number=None)
    assert resolve_opening_number("five_verst", silenced) is None
    assert resolve_opening_number("s95", LocationOpening(location_id=uuid4(), opening_event_number=3)) == 3


def _seed_admin(db_session: Session) -> User:
    """Кто правит разметку: в строке остаётся его имя (updated_by)."""
    user = User(
        display_name="Admin Tester",
        telegram_id=int(uuid4().int % 1_000_000_000),
    )
    db_session.add(user)
    db_session.flush()
    return user


def _platform(db_session: Session, code: str) -> Platform:
    platform = db_session.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=code)
        db_session.add(platform)
        db_session.flush()
    return platform


def _seed_location_with_runs(
    db_session: Session,
    *,
    platform_code: str,
    numbers: list[int],
    participant_id: UUID | None = None,
    start_date: date = date(2024, 1, 6),
) -> tuple[UUID, UUID, dict[int, UUID]]:
    """Площадка с несколькими стартами, участник бежал на каждом из numbers."""
    suffix = str(uuid4().int % 1_000_000)
    platform = _platform(db_session, platform_code)

    if participant_id is None:
        participant = Participant(
            platform_id=platform.id,
            external_user_id=f"openings-user-{suffix}",
            display_name="Openings Tester",
        )
        db_session.add(participant)
        db_session.flush()
        participant_id = participant.id

    location = Location(
        platform_id=platform.id,
        external_key=f"openings-{platform_code}-{suffix}",
        name="Openings Park",
        city="Москва",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    # Номер и дата разведены: в тестах бывает и повторяющийся номер (неполная
    # история С95), а ключ события и дата у каждого старта всё равно свои.
    events: dict[int, UUID] = {}
    for index, number in enumerate(numbers):
        event = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"openings-event-{suffix}-{index}",
            event_date=start_date + timedelta(days=index),
            event_number=number,
            title="Openings Event",
        )
        db_session.add(event)
        db_session.flush()
        events.setdefault(number, event.id)
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=participant_id,
                external_result_key=f"openings-result-{suffix}-{index}",
                position=10,
                finish_time_sec=25 * 60,
                finish_time_display="00:25:00",
                status="finished",
            )
        )
    db_session.flush()
    return participant_id, location.id, events


def test_openings_count_first_event_of_five_verst(db_session: Session) -> None:
    participant_id, _location_id, _events = _seed_location_with_runs(
        db_session, platform_code="five_verst", numbers=[1, 2, 3]
    )
    values, total, _week, last = _my_opening_values(
        db_session, [participant_id], date(2026, 8, 8)
    )
    # Три старта на площадке, открытие среди них ровно одно.
    assert total == 1
    assert values["five_verst"][0] == 1
    assert last is not None and last.on_date == date(2024, 1, 6)


def test_openings_skip_s95_until_marked_by_hand(db_session: Session) -> None:
    """С95 без разметки открытий не даёт: по номерам её забегов открытие не опознать."""
    participant_id, location_id, _events = _seed_location_with_runs(
        db_session, platform_code="s95", numbers=[1, 2, 3]
    )
    _values, total, _week, _last = _my_opening_values(
        db_session, [participant_id], date(2026, 8, 8)
    )
    assert total == 0

    admin = _seed_admin(db_session)
    set_opening(db_session, location_id, opening_event_number=3, note=None, admin=admin)
    values, total, _week, _last = _my_opening_values(
        db_session, [participant_id], date(2026, 8, 8)
    )
    # Открытием считается именно размеченный старт, а не первый по счёту.
    assert total == 1
    assert values["s95"][0] == 1


def test_manual_empty_number_silences_protocol_opening(db_session: Session) -> None:
    """Ложное открытие гасится строкой с пустым номером."""
    participant_id, location_id, _events = _seed_location_with_runs(
        db_session, platform_code="runpark", numbers=[1, 2]
    )
    admin = _seed_admin(db_session)
    set_opening(db_session, location_id, opening_event_number=None, note="площадка старше", admin=admin)
    _values, total, _week, _last = _my_opening_values(
        db_session, [participant_id], date(2026, 8, 8)
    )
    assert total == 0

    # «Снять» возвращает площадку к правилу системы.
    clear_opening(db_session, location_id)
    _values, total, _week, _last = _my_opening_values(
        db_session, [participant_id], date(2026, 8, 8)
    )
    assert total == 1


def test_openings_count_once_when_number_repeats(db_session: Session) -> None:
    """Дублирующийся номер (неполная история С95) даёт одно открытие, не два."""
    participant_id, _location_id, _events = _seed_location_with_runs(
        db_session, platform_code="five_verst", numbers=[1, 1]
    )
    _values, total, _week, last = _my_opening_values(
        db_session, [participant_id], date(2026, 8, 8)
    )
    assert total == 1
    # Открытием считается самый ранний из одинаково пронумерованных стартов.
    assert last is not None and last.on_date == date(2024, 1, 6)


def _link_locations_as_one_place(db_session: Session, location_ids: list[UUID]) -> None:
    """Связать локации разных систем в одну физическую точку каталога."""
    catalog = LocationCatalog(
        canonical_name=f"Openings Park {uuid4().int % 1_000_000}",
        active_platform="five_verst",
        is_closed=False,
    )
    db_session.add(catalog)
    db_session.flush()
    for location_id in location_ids:
        location = db_session.query(Location).filter(Location.id == location_id).one()
        db_session.add(
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=location.platform_id,
                external_key=location.external_key,
                location_id=location.id,
            )
        )
    db_session.flush()


def test_one_place_gives_one_opening_even_in_two_systems(db_session: Session) -> None:
    """Локация открывается один раз: parkrun-эра и «5 вёрст» — одно открытие.

    Решение Дмитрия 16.08.2026: парк открывали при parkrun — значит открытие
    именно то, а первый старт следующей системы им уже не считается.
    """
    # parkrun-эра раньше «5 вёрст» — она и должна дать открытие.
    participant_id, parkrun_loc, _events = _seed_location_with_runs(
        db_session, platform_code="parkrun", numbers=[1], start_date=date(2018, 5, 12)
    )
    _same, five_verst_loc, _events2 = _seed_location_with_runs(
        db_session,
        platform_code="five_verst",
        numbers=[1],
        participant_id=participant_id,
        start_date=date(2022, 4, 2),
    )
    _link_locations_as_one_place(db_session, [parkrun_loc, five_verst_loc])

    values, total, _week, _last = _my_opening_values(
        db_session, [participant_id], date(2026, 8, 8)
    )
    assert total == 1
    # В зачёт идёт самое раннее открытие — parkrun (его старт засеян первым).
    assert values["parkrun"][0] == 1
    assert "five_verst" not in values


def test_later_system_opening_does_not_count(db_session: Session) -> None:
    """Пришёл только на первый старт следующей системы — открытия не засчитано."""
    other_runner, parkrun_loc, _events = _seed_location_with_runs(
        db_session, platform_code="parkrun", numbers=[1], start_date=date(2018, 5, 12)
    )
    latecomer, five_verst_loc, _events2 = _seed_location_with_runs(
        db_session, platform_code="five_verst", numbers=[1], start_date=date(2022, 4, 2)
    )
    _link_locations_as_one_place(db_session, [parkrun_loc, five_verst_loc])

    _values, total, _week, _last = _my_opening_values(
        db_session, [latecomer], date(2026, 8, 8)
    )
    assert total == 0
    # А тому, кто был на самом открытии, оно засчитано.
    _values, total, _week, _last = _my_opening_values(
        db_session, [other_runner], date(2026, 8, 8)
    )
    assert total == 1


def test_openings_count_each_system_separately(db_session: Session) -> None:
    """Разные физические локации дают по открытию каждая — их не схлопывает."""
    participant_id, _loc, _events = _seed_location_with_runs(
        db_session, platform_code="five_verst", numbers=[1]
    )
    _same_person, _loc2, _events2 = _seed_location_with_runs(
        db_session, platform_code="runpark", numbers=[1], participant_id=participant_id
    )
    values, total, _week, _last = _my_opening_values(
        db_session, [participant_id], date(2026, 8, 8)
    )
    assert total == 2
    assert values["five_verst"][0] == 1
    assert values["runpark"][0] == 1


def test_openings_do_not_double_count_crosslinked_protocols(db_session: Session) -> None:
    """Один физический старт в протоколах двух систем — одно открытие."""
    participant_id, _loc, primary_events = _seed_location_with_runs(
        db_session, platform_code="five_verst", numbers=[1]
    )
    _same_person, _loc2, secondary_events = _seed_location_with_runs(
        db_session, platform_code="runpark", numbers=[1], participant_id=participant_id
    )
    db_session.add(
        EventCrosslink(
            primary_event_id=primary_events[1],
            secondary_event_id=secondary_events[1],
        )
    )
    db_session.flush()
    values, total, _week, _last = _my_opening_values(
        db_session, [participant_id], date(2026, 8, 8)
    )
    assert total == 1
    assert "runpark" not in values


def test_list_openings_marks_source_and_preview(db_session: Session) -> None:
    _participant, location_id, _events = _seed_location_with_runs(
        db_session, platform_code="s95", numbers=[1, 2, 3]
    )
    payload = list_openings(db_session, platform="s95")
    item = next(row for row in payload["items"] if row["location_id"] == location_id)
    # До разметки — «открытия нет», но первые старты подсказкой уже видны.
    assert item["opening_source"] == "none"
    assert item["opening_event"] is None
    assert [event["event_number"] for event in item["first_events"]] == [1, 2, 3]
    # Число финишёров считаем сами: у С95 и parkrun протокол его не приносит.
    assert item["first_events"][0]["finishers"] == 1

    admin = _seed_admin(db_session)
    set_opening(db_session, location_id, opening_event_number=2, note="открытие", admin=admin)
    payload = list_openings(db_session, platform="s95")
    item = next(row for row in payload["items"] if row["location_id"] == location_id)
    assert item["opening_source"] == "manual"
    assert item["opening_event_number"] == 2
    assert item["opening_event"]["event_number"] == 2
    assert item["note"] == "открытие"
    assert [event["is_opening"] for event in item["first_events"]] == [False, True, False]


def test_list_openings_flags_number_without_event(db_session: Session) -> None:
    """Опечатка в номере не должна молча выключать открытие."""
    _participant, location_id, _events = _seed_location_with_runs(
        db_session, platform_code="s95", numbers=[1, 2]
    )
    admin = _seed_admin(db_session)
    set_opening(db_session, location_id, opening_event_number=99, note=None, admin=admin)
    payload = list_openings(db_session, platform="s95")
    item = next(row for row in payload["items"] if row["location_id"] == location_id)
    assert item["opening_event_missing"] is True
    assert item["opening_event"] is None
