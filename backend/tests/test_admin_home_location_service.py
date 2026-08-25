"""Домашняя площадка пачкой (админка) и срез регистраций по городам."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from itertools import combinations
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
    VolunteerResult,
)
from app.services.admin_home_location_service import resolve_admin_home_locations
from app.services.admin_site_stats_service import _combo_sort_key, _link_combinations
from app.services.admin_users_geo_stats_service import get_admin_users_geography
from app.services.location_catalog_service import LocationCatalogIndex


def _user(db_session: Session, *, created_at: datetime | None = None) -> User:
    user = User(
        telegram_id=int(uuid4().int % 10_000_000_000),
        display_name="Админский тестовый",
        consent_accepted=True,
    )
    if created_at is not None:
        user.created_at = created_at
    db_session.add(user)
    db_session.flush()
    return user


def _participant(db_session: Session, user: User, platform_code: str = "five_verst") -> Participant:
    platform = db_session.query(Platform).filter(Platform.code == platform_code).one()
    external_user_id = str(uuid4().int % 1_000_000_000)
    participant = Participant(
        platform_id=platform.id,
        external_user_id=external_user_id,
        display_name="Админский тестовый",
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


def _location(db_session: Session, name: str, city: str) -> Location:
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    slug = f"admin-home-{uuid4().hex[:10]}"
    location = Location(
        platform_id=platform.id,
        external_key=slug,
        name=name,
        city=city,
        region=city,
        country="Россия",
        is_official_map=True,
    )
    db_session.add(location)
    db_session.flush()
    return location


def _event(db_session: Session, location: Location, event_date: date) -> Event:
    """Старт на площадке в этот день. Он один на всех участников — в базе
    (platform, location, event_date) уникальны."""
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    existing = (
        db_session.query(Event)
        .filter(
            Event.platform_id == platform.id,
            Event.location_id == location.id,
            Event.event_date == event_date,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing
    key = f"admin-home:{location.external_key}:{event_date.isoformat()}:{uuid4().hex[:6]}"
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=key,
        event_date=event_date,
        event_number=1,
        title=f"Тестовый старт {location.name}",
    )
    db_session.add(event)
    db_session.flush()
    return event


def _seed_runs(
    db_session: Session, participant: Participant, location: Location, dates: list[date]
) -> None:
    for event_date in dates:
        event = _event(db_session, location, event_date)
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"{event.external_event_key}:{participant.external_user_id}",
                position=1,
                finish_time_sec=1500,
                finish_time_display="00:25:00",
                status="finished",
            )
        )
    db_session.flush()


def _seed_volunteering(
    db_session: Session, participant: Participant, location: Location, dates: list[date]
) -> None:
    for event_date in dates:
        event = _event(db_session, location, event_date)
        db_session.add(
            VolunteerResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"{event.external_event_key}:vol:{participant.external_user_id}",
                role="Маршал",
            )
        )
    db_session.flush()


def test_home_is_location_with_most_run_days(db_session: Session) -> None:
    user = _user(db_session)
    participant = _participant(db_session, user)
    home = _location(db_session, "Домашняя", "Москва")
    away = _location(db_session, "Гостевая", "Казань")
    _seed_runs(db_session, participant, home, [date(2026, 3, 7), date(2026, 3, 14), date(2026, 3, 21)])
    _seed_runs(db_session, participant, away, [date(2026, 4, 4)])

    result = resolve_admin_home_locations(db_session, [user.id])

    assert result[user.id].name == "Домашняя"
    assert result[user.id].city == "Москва"
    assert result[user.id].run_days == 3
    assert result[user.id].locations_total == 2
    assert result[user.id].is_manual is False
    assert result[user.id].is_tie is False
    assert result[user.id].tied == []


def test_equal_runs_are_resolved_by_earlier_first_run(db_session: Session) -> None:
    """Ничья по пробежкам и волонтёрствам — дом там, где человек начал раньше
    (третья ступень рейтинга). Ничьей это не считается."""
    user = _user(db_session)
    participant = _participant(db_session, user)
    earlier = _location(db_session, "Ранняя", "Москва")
    later = _location(db_session, "Поздняя", "Москва")
    _seed_runs(db_session, participant, earlier, [date(2026, 3, 7), date(2026, 3, 14)])
    _seed_runs(db_session, participant, later, [date(2026, 3, 21), date(2026, 3, 28)])

    result = resolve_admin_home_locations(db_session, [user.id])[user.id]

    assert result.name == "Ранняя"
    assert result.is_tie is False


def test_full_tie_lists_all_top_locations(db_session: Session) -> None:
    """Совпали и пробежки, и волонтёрства, и дата первой пробежки — правило
    исчерпано: в админку отдаём весь список претендентов."""
    user = _user(db_session)
    participant = _participant(db_session, user)
    same_day = date(2026, 3, 7)
    first = _location(db_session, "Альфа", "Москва")
    second = _location(db_session, "Бета", "Москва")
    third = _location(db_session, "Гамма", "Москва")
    for location in (first, second, third):
        _seed_runs(db_session, participant, location, [same_day])

    result = resolve_admin_home_locations(db_session, [user.id])[user.id]

    assert result.is_tie is True
    assert [item.name for item in result.tied] == ["Альфа", "Бета", "Гамма"]
    # Строка всё равно называет одну площадку — ту же, что выбрал бы рейтинг.
    assert result.name == "Альфа"


def test_manual_choice_wins_over_auto_pick(db_session: Session) -> None:
    user = _user(db_session)
    participant = _participant(db_session, user)
    most_runs = _location(db_session, "Где чаще всего", "Москва")
    chosen = _location(db_session, "Выбранная руками", "Казань")
    _seed_runs(db_session, participant, most_runs, [date(2026, 3, 7), date(2026, 3, 14)])
    _seed_runs(db_session, participant, chosen, [date(2026, 3, 21)])
    _seed_volunteering(db_session, participant, chosen, [date(2026, 4, 4)])

    auto = resolve_admin_home_locations(db_session, [user.id])[user.id]
    assert auto.name == "Где чаще всего"

    chosen_key = LocationCatalogIndex(db_session).canonical_identity_key(chosen, "five_verst")
    user.home_location_key = chosen_key
    db_session.flush()
    result = resolve_admin_home_locations(db_session, [user.id])[user.id]
    assert result.identity_key == chosen_key
    assert result.name == "Выбранная руками"
    assert result.is_manual is True
    # У ручного выбора претендентов не перебираем: человек решил сам.
    assert result.is_tie is False
    assert result.tied == []


def test_user_without_runs_has_no_home(db_session: Session) -> None:
    user = _user(db_session)
    _participant(db_session, user)

    assert resolve_admin_home_locations(db_session, [user.id]) == {}


def test_geography_groups_registrations_by_city(db_session: Session) -> None:
    now = datetime.now(timezone.utc)
    city_location = _location(db_session, "Городская", "Тверь")
    for _ in range(2):
        user = _user(db_session, created_at=now - timedelta(days=1))
        _seed_runs(db_session, _participant(db_session, user), city_location, [date(2026, 3, 7)])
    old_user = _user(db_session, created_at=now - timedelta(days=200))
    _seed_runs(db_session, _participant(db_session, old_user), city_location, [date(2026, 3, 14)])
    db_session.flush()

    payload = get_admin_users_geography(db_session, period_days=30)

    cities = {str(row["city"]): row for row in payload["cities"]}  # type: ignore[union-attr]
    assert cities["Тверь"]["users"] == 3
    assert cities["Тверь"]["users_new_period"] == 2
    assert cities["Тверь"]["locations"] == 1

    locations = {str(row["name"]): row for row in payload["locations"]}  # type: ignore[union-attr]
    assert locations["Городская"]["users"] == 3
    assert locations["Городская"]["users_new_period"] == 2


def test_link_combinations_cover_every_platform_set(db_session: Session) -> None:
    """Считаем ВСЕ сочетания систем, а не только те, где есть 5 вёрст.

    Человек с С95 и parkrun должен попасть в свою строку — и ровно в одну:
    наборы точные, иначе суммы по строкам разъехались бы с числом людей.
    """
    user = _user(db_session)
    for platform_code in ("s95", "parkrun"):
        _participant(db_session, user, platform_code)
    db_session.flush()

    rows = _link_combinations(db_session)
    by_codes = {tuple(row["codes"]): int(row["users"]) for row in rows}  # type: ignore[arg-type]

    # Все 15 непустых сочетаний четырёх систем присутствуют, даже нулевые.
    known = ("five_verst", "s95", "parkrun", "runpark")
    for size in range(1, len(known) + 1):
        for combo in combinations(known, size):
            assert tuple(sorted(combo, key=_combo_sort_key)) in by_codes

    assert by_codes[("s95", "parkrun")] >= 1
    # Тот же человек не должен посчитаться ещё и в односистемных строках —
    # проверяем через сумму: она равна числу людей с привязками.
    linked_users = (
        db_session.query(PlatformLink.user_id).filter(PlatformLink.user_id.isnot(None)).distinct().count()
    )
    assert sum(by_codes.values()) == linked_users
