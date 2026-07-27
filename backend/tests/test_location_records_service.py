"""Рекорды локаций участника: прогрессия, уровни, утерянные рекорды, вехи.

Смысл фичи: на «Обзоре» две плитки — сколько рекордов локаций (лучшее время
площадки в рамках пола) и сколько рекордов по возрастным группам держит
участник. Рекорд «на момент забега» восстанавливается из истории протоколов,
поэтому сервис знает и утерянные рекорды: кто, когда и каким временем перебил.
Для площадок из нескольких систем рекорд двухуровневый (система/глобальный).
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import (
    Event,
    Location,
    LocationCatalog,
    LocationCatalogLink,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
)
from app.services.location_records_service import compute_user_location_records


def _platform(db: Session, code: str, name: str) -> Platform:
    platform = db.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=name)
        db.add(platform)
        db.flush()
    return platform


def _participant(
    db: Session,
    platform: Platform,
    suffix: str,
    name: str,
    *,
    gender: str | None = None,
    age_category: str | None = None,
) -> Participant:
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"locrec-{suffix}",
        display_name=name,
        gender=gender,
        age_category=age_category,
    )
    db.add(participant)
    db.flush()
    return participant


def _link_user(db: Session, links: list[tuple[Platform, Participant]]) -> User:
    user = User(telegram_id=int(uuid4().int % 1_000_000_000), display_name="Тестовый Бегун")
    db.add(user)
    db.flush()
    for platform, participant in links:
        db.add(
            PlatformLink(
                user_id=user.id,
                platform_id=platform.id,
                participant_id=participant.id,
                external_user_id=participant.external_user_id,
                external_url="https://example.com",
            )
        )
    db.flush()
    return user


def _location(db: Session, platform: Platform, suffix: str, name: str) -> Location:
    location = Location(
        platform_id=platform.id,
        external_key=f"locrec-{suffix}",
        name=name,
        city="Москва",
    )
    db.add(location)
    db.flush()
    return location


def _event(db: Session, platform: Platform, location: Location, day: date) -> Event:
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"locrec-event-{uuid4().hex[:10]}",
        event_date=day,
        title=location.name,
    )
    db.add(event)
    db.flush()
    return event


def _result(
    db: Session,
    event: Event,
    participant: Participant,
    *,
    finish_time_sec: int | None,
    age_category: str | None = "М30-34",
    position: int | None = None,
) -> None:
    db.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"{event.external_event_key}:{participant.external_user_id}:{uuid4().hex[:6]}",
            finish_time_sec=finish_time_sec,
            age_category=age_category,
            position=position,
            status="finished",
        )
    )
    db.flush()


def test_mono_location_record_set_then_lost(db_session: Session) -> None:
    """Рекорд монолокации: установлен → перебит; запись серая с деталями."""
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    runner = _participant(db_session, five_verst, "runner", "Бегун", gender="male")
    rival = _participant(db_session, five_verst, "rival", "Соперник")
    user = _link_user(db_session, [(five_verst, runner)])
    location = _location(db_session, five_verst, "mono", "Монопарк")

    _result(db_session, _event(db_session, five_verst, location, date(2025, 1, 4)), runner, finish_time_sec=1100)
    _result(db_session, _event(db_session, five_verst, location, date(2025, 2, 1)), rival, finish_time_sec=1050)

    payload = compute_user_location_records(db_session, user.id)
    course = payload["course"]
    assert course["current_count"] == 0
    assert course["lost_count"] == 1
    entry = course["entries"][0]
    assert entry["location_name"] == "Монопарк"
    assert entry["level"] is None  # монолокация — уровень не показываем
    assert entry["is_current"] is False
    assert entry["beaten_date"] == "2025-02-01"
    assert entry["beaten_by"] == "Соперник"
    assert entry["beaten_time_sec"] == 1050

    kinds = [row["kind"] for row in payload["milestones"]]
    assert "location_course_record" in kinds


def test_consecutive_improvements_collapse_into_one_entry(db_session: Session) -> None:
    """Улучшения собственного рекорда — одна запись витрины, но веха на каждое."""
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    runner = _participant(db_session, five_verst, "runner2", "Бегун", gender="male")
    user = _link_user(db_session, [(five_verst, runner)])
    location = _location(db_session, five_verst, "mono2", "Монопарк-2")

    _result(db_session, _event(db_session, five_verst, location, date(2025, 1, 4)), runner, finish_time_sec=1100)
    _result(db_session, _event(db_session, five_verst, location, date(2025, 3, 1)), runner, finish_time_sec=1080)

    payload = compute_user_location_records(db_session, user.id)
    course = payload["course"]
    assert course["current_count"] == 1
    assert course["lost_count"] == 0
    entry = course["entries"][0]
    assert entry["finish_time_sec"] == 1080
    assert entry["event_date"] == "2025-03-01"

    course_milestones = [row for row in payload["milestones"] if row["kind"] == "location_course_record"]
    assert [row["finish_time_sec"] for row in course_milestones] == [1100, 1080]


def test_equal_time_does_not_take_record(db_session: Session) -> None:
    """Повторение времени рекорда не забирает рекорд у автора."""
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    runner = _participant(db_session, five_verst, "runner3", "Бегун", gender="male")
    rival = _participant(db_session, five_verst, "rival3", "Соперник")
    user = _link_user(db_session, [(five_verst, runner)])
    location = _location(db_session, five_verst, "mono3", "Монопарк-3")

    _result(db_session, _event(db_session, five_verst, location, date(2025, 1, 4)), runner, finish_time_sec=1100)
    _result(db_session, _event(db_session, five_verst, location, date(2025, 2, 1)), rival, finish_time_sec=1100)

    payload = compute_user_location_records(db_session, user.id)
    course = payload["course"]
    assert course["current_count"] == 1
    assert course["lost_count"] == 0


def test_zero_and_null_times_ignored(db_session: Session) -> None:
    """Нулевые и пустые времена («НЕИЗВЕСТНЫЙ» в протоколах) не участвуют."""
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    runner = _participant(db_session, five_verst, "runner4", "Бегун", gender="male")
    ghost = _participant(db_session, five_verst, "ghost4", "Неизвестный")
    user = _link_user(db_session, [(five_verst, runner)])
    location = _location(db_session, five_verst, "mono4", "Монопарк-4")

    event = _event(db_session, five_verst, location, date(2025, 1, 4))
    _result(db_session, event, ghost, finish_time_sec=0)
    _result(db_session, event, ghost, finish_time_sec=None)
    _result(db_session, _event(db_session, five_verst, location, date(2025, 2, 1)), runner, finish_time_sec=1200)

    payload = compute_user_location_records(db_session, user.id)
    assert payload["course"]["current_count"] == 1


def test_multi_system_levels(db_session: Session) -> None:
    """Мультисистемная площадка: рекорд системы против глобального.

    Parkrun-эра быстрее: результат пользователя на 5 вёрст — рекорд системы
    (level=five_verst), но не глобальный. Когда пользователь бежит быстрее
    parkrun-эры, запись схлопывается в одну глобальную, а веха помечается
    охватом global.
    """
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    parkrun = _platform(db_session, "parkrun", "parkrun")
    runner = _participant(db_session, five_verst, "runner5", "Бегун", gender="male")
    legend = _participant(db_session, parkrun, "legend5", "Легенда", age_category="SM30-34")
    user = _link_user(db_session, [(five_verst, runner)])

    fv_location = _location(db_session, five_verst, "multi-fv", "Дружба")
    pr_location = _location(db_session, parkrun, "multi-pr", "Druzhba")
    catalog = LocationCatalog(canonical_name="Дружба", active_platform="five_verst")
    db_session.add(catalog)
    db_session.flush()
    db_session.add_all(
        [
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=five_verst.id,
                external_key=fv_location.external_key,
                location_id=fv_location.id,
            ),
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=parkrun.id,
                external_key=pr_location.external_key,
                location_id=pr_location.id,
            ),
        ]
    )
    db_session.flush()

    # Parkrun-эра: глобальный рекорд 1000 (2019).
    _result(
        db_session,
        _event(db_session, parkrun, pr_location, date(2019, 6, 1)),
        legend,
        finish_time_sec=1000,
        age_category=None,  # у parkrun пол берётся из participants.age_category
    )
    # Пользователь ставит рекорд 5 вёрст (1050) — медленнее parkrun-эры.
    _result(db_session, _event(db_session, five_verst, fv_location, date(2025, 1, 4)), runner, finish_time_sec=1050)

    payload = compute_user_location_records(db_session, user.id)
    course = payload["course"]
    assert course["current_count"] == 1
    entry = course["entries"][0]
    assert entry["level"] == "five_verst"

    milestone = next(row for row in payload["milestones"] if row["kind"] == "location_course_record")
    assert milestone["record_scope"] == "five_verst"

    # Теперь пользователь бежит быстрее parkrun-эры: глобальный рекорд.
    _result(db_session, _event(db_session, five_verst, fv_location, date(2025, 2, 1)), runner, finish_time_sec=990)

    payload = compute_user_location_records(db_session, user.id)
    course = payload["course"]
    # Системная серия схлопнута в глобальную: одна действующая запись.
    assert course["current_count"] == 1
    assert course["entries"][0]["level"] == "global"

    scopes = [
        row["record_scope"]
        for row in payload["milestones"]
        if row["kind"] == "location_course_record"
    ]
    assert "global" in scopes


def test_age_group_records_only_five_verst_and_gender_scoped(db_session: Session) -> None:
    """Возрастные рекорды: только 5 вёрст, женское время не бьёт мужской рекорд группы."""
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    runner = _participant(db_session, five_verst, "runner6", "Бегун", gender="male")
    lady = _participant(db_session, five_verst, "lady6", "Бегунья")
    user = _link_user(db_session, [(five_verst, runner)])
    location = _location(db_session, five_verst, "mono6", "Монопарк-6")

    _result(db_session, _event(db_session, five_verst, location, date(2025, 1, 4)), runner, finish_time_sec=1150)
    # Более быстрая женщина той же группы — не соперница в мужской ветке.
    _result(
        db_session,
        _event(db_session, five_verst, location, date(2025, 2, 1)),
        lady,
        finish_time_sec=1100,
        age_category="Ж30-34",
    )

    payload = compute_user_location_records(db_session, user.id)
    age = payload["age_group"]
    assert age["current_count"] == 1
    entry = age["entries"][0]
    assert entry["age_group"] == "30–34"
    assert entry["is_current"] is True

    milestone = next(row for row in payload["milestones"] if row["kind"] == "location_age_group_record")
    assert milestone["age_group"] == "30–34"


def test_no_gender_no_course_records(db_session: Session) -> None:
    """Без известного пола участника рекорды не считаются (и не падают)."""
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    runner = _participant(db_session, five_verst, "runner7", "Бегун")
    user = _link_user(db_session, [(five_verst, runner)])
    location = _location(db_session, five_verst, "mono7", "Монопарк-7")
    _result(db_session, _event(db_session, five_verst, location, date(2025, 1, 4)), runner, finish_time_sec=1100)

    payload = compute_user_location_records(db_session, user.id)
    assert payload["course"]["current_count"] == 0
    assert payload["age_group"]["current_count"] == 0
    assert payload["milestones"] == []
