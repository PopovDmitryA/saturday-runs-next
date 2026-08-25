"""Рейтинг рекордов локаций: абсолютный зачёт, возрастной и фильтр по системе.

Смысл фичи: одна таблица со всеми площадками страны, где место определяет время
рекорда. Абсолютный зачёт работает у всех систем, возрастной — только у 5 вёрст,
и тесты закрепляют именно эту границу: площадка без возрастных категорий обязана
остаться в абсолютном рейтинге и пропасть из возрастного.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult, User
from app.services.location_records_rating_service import (
    build_location_records_rating,
    viewer_age_group,
)


def _platform(db: Session, code: str, name: str) -> Platform:
    platform = db.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=name)
        db.add(platform)
        db.flush()
    return platform


def _location(db: Session, platform: Platform, suffix: str, name: str) -> Location:
    location = Location(
        platform_id=platform.id,
        external_key=f"locrec-{suffix}",
        name=name,
        city="Москва",
        region="Москва",
        is_official_map=True,
    )
    db.add(location)
    db.flush()
    return location


def _event(db: Session, platform: Platform, location: Location, suffix: str, day: date) -> Event:
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"locrec-event-{suffix}",
        event_date=day,
        event_number=int(uuid4().int % 100_000) + 800_000,
        title=location.name,
    )
    db.add(event)
    db.flush()
    return event


def _participant(db: Session, platform: Platform, suffix: str, name: str, **kwargs: object) -> Participant:
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"locrec-{suffix}",
        display_name=name,
        **kwargs,
    )
    db.add(participant)
    db.flush()
    return participant


def _result(
    db: Session,
    event: Event,
    participant: Participant,
    *,
    finish_time_sec: int,
    age_category: str | None = None,
) -> None:
    db.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"{event.external_event_key}:{participant.external_user_id}",
            finish_time_sec=finish_time_sec,
            age_category=age_category,
            status="finished",
        )
    )


def _row(payload: dict[str, object], slug: str) -> dict[str, object] | None:
    rows = payload["rows"]
    assert isinstance(rows, list)
    return next((row for row in rows if row["slug"] == slug), None)


def test_absolute_rating_shows_record_holder_and_protocol_link(db_session: Session) -> None:
    suffix = uuid4().hex[:8]
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    location = _location(db_session, five_verst, f"abs-{suffix}", "Рекордный парк")
    fast_event = _event(db_session, five_verst, location, f"abs-fast-{suffix}", date(2024, 3, 2))
    slow_event = _event(db_session, five_verst, location, f"abs-slow-{suffix}", date(2024, 3, 9))

    winner = _participant(db_session, five_verst, f"abs-win-{suffix}", "Быстрый Бегун")
    other = _participant(db_session, five_verst, f"abs-oth-{suffix}", "Обычный Бегун")
    _result(db_session, fast_event, winner, finish_time_sec=900, age_category="М30-34")
    _result(db_session, slow_event, other, finish_time_sec=1200, age_category="М30-34")
    db_session.commit()

    payload = build_location_records_rating(db_session, scope="absolute", gender="male", use_cache=False)
    row = _row(payload, f"locrec-abs-{suffix}")
    assert row is not None
    assert row["finish_time_sec"] == 900
    assert row["runner_name"] == "Быстрый Бегун"
    assert row["event_date"] == "2024-03-02"
    # Дата ведёт в наш протокол этого старта, а не на сайт системы.
    assert row["protocol_url"] == f"/locations/locrec-abs-{suffix}/protocol/five_verst/2024-03-02"


def test_places_are_ranked_by_record_time(db_session: Session) -> None:
    suffix = uuid4().hex[:8]
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    quick = _location(db_session, five_verst, f"quick-{suffix}", "Быстрая трасса")
    slow = _location(db_session, five_verst, f"slow-{suffix}", "Медленная трасса")
    quick_event = _event(db_session, five_verst, quick, f"quick-{suffix}", date(2024, 4, 6))
    slow_event = _event(db_session, five_verst, slow, f"slow-{suffix}", date(2024, 4, 6))

    _result(
        db_session,
        quick_event,
        _participant(db_session, five_verst, f"quick-r-{suffix}", "Первый"),
        finish_time_sec=880,
        age_category="М35-39",
    )
    _result(
        db_session,
        slow_event,
        _participant(db_session, five_verst, f"slow-r-{suffix}", "Второй"),
        finish_time_sec=1500,
        age_category="М35-39",
    )
    db_session.commit()

    payload = build_location_records_rating(db_session, scope="absolute", gender="male", use_cache=False)
    quick_row = _row(payload, f"locrec-quick-{suffix}")
    slow_row = _row(payload, f"locrec-slow-{suffix}")
    assert quick_row is not None and slow_row is not None
    # Место — по времени рекорда: быстрая трасса всегда выше медленной.
    assert int(quick_row["place"]) < int(slow_row["place"])  # type: ignore[arg-type]


def test_age_group_scope_keeps_only_five_verst(db_session: Session) -> None:
    """У S95 возрастной категории в протоколе нет — в возрастном зачёте её быть не должно."""
    suffix = uuid4().hex[:8]
    s95 = _platform(db_session, "s95", "С95")
    location = _location(db_session, s95, f"s95-{suffix}", "Площадка С95")
    event = _event(db_session, s95, location, f"s95-{suffix}", date(2024, 5, 4))
    runner = _participant(
        db_session,
        s95,
        f"s95-r-{suffix}",
        "Сергей С95",
        profile_extra={"platform_codes": {"gender": "male"}},
    )
    _result(db_session, event, runner, finish_time_sec=1000)
    db_session.commit()

    absolute = build_location_records_rating(db_session, scope="absolute", gender="male", use_cache=False)
    assert _row(absolute, f"locrec-s95-{suffix}") is not None

    age = build_location_records_rating(
        db_session, scope="age_group", gender="male", age_group="30–34", use_cache=False
    )
    assert _row(age, f"locrec-s95-{suffix}") is None


def test_age_group_scope_takes_record_of_the_chosen_group(db_session: Session) -> None:
    suffix = uuid4().hex[:8]
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    location = _location(db_session, five_verst, f"age-{suffix}", "Возрастной парк")
    event = _event(db_session, five_verst, location, f"age-{suffix}", date(2024, 6, 1))

    young = _participant(db_session, five_verst, f"age-young-{suffix}", "Молодой Бегун")
    veteran = _participant(db_session, five_verst, f"age-vet-{suffix}", "Ветеран Бегун")
    _result(db_session, event, young, finish_time_sec=950, age_category="М30-34")
    _result(db_session, event, veteran, finish_time_sec=1300, age_category="М60-64")
    db_session.commit()

    payload = build_location_records_rating(
        db_session, scope="age_group", gender="male", age_group="60–64", use_cache=False
    )
    row = _row(payload, f"locrec-age-{suffix}")
    assert row is not None
    # Рекорд группы «60–64» — время ветерана, а не абсолютный рекорд площадки.
    assert row["finish_time_sec"] == 1300
    assert row["runner_name"] == "Ветеран Бегун"


def test_platform_filter_shows_record_inside_one_system(db_session: Session) -> None:
    """У площадки с двумя эпохами общий рекорд один, а рекорд системы — свой."""
    suffix = uuid4().hex[:8]
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    location = _location(db_session, five_verst, f"mix-{suffix}", "Двухэпоховый парк")
    event = _event(db_session, five_verst, location, f"mix-{suffix}", date(2024, 7, 6))
    _result(
        db_session,
        event,
        _participant(db_session, five_verst, f"mix-r-{suffix}", "Бегун 5 вёрст"),
        finish_time_sec=1100,
        age_category="М25-29",
    )
    db_session.commit()

    same_system = build_location_records_rating(
        db_session, scope="absolute", gender="male", platform="five_verst", use_cache=False
    )
    assert _row(same_system, f"locrec-mix-{suffix}") is not None

    other_system = build_location_records_rating(
        db_session, scope="absolute", gender="male", platform="s95", use_cache=False
    )
    # В зачёте другой системы этой площадки нет: своих протоколов там не было.
    assert _row(other_system, f"locrec-mix-{suffix}") is None


def test_viewer_age_group_takes_the_latest_category(db_session: Session) -> None:
    suffix = uuid4().hex[:8]
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    location = _location(db_session, five_verst, f"viewer-{suffix}", "Парк зрителя")
    old_event = _event(db_session, five_verst, location, f"viewer-old-{suffix}", date(2023, 1, 7))
    new_event = _event(db_session, five_verst, location, f"viewer-new-{suffix}", date(2025, 1, 4))

    participant = _participant(db_session, five_verst, f"viewer-{suffix}", "Я Зритель")
    user = User(telegram_id=int(uuid4().int % 1_000_000_000), display_name="Я Зритель")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=five_verst.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url=f"https://5verst.ru/userstats/{participant.external_user_id}/",
        )
    )
    _result(db_session, old_event, participant, finish_time_sec=1400, age_category="М34-39")
    _result(db_session, new_event, participant, finish_time_sec=1450, age_category="М40-44")
    db_session.commit()

    # Категория меняется с возрастом — актуальна последняя по дате.
    assert viewer_age_group(db_session, user.id) == {"gender": "male", "age_group": "40–44"}
    assert viewer_age_group(db_session, None) is None
