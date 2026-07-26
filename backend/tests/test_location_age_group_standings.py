"""Место участника в возрастной группе локации и топ-5 этой группы.

Смысл фичи: у участника столько плиток «место в группе», сколько возрастных
категорий он успел пройти на этой площадке, и в каждой — своё место среди тех,
кто бежал здесь в той же категории. Сравнение всегда внутри группы: лучшее
время в «30–34» не конкурирует с временами «35–39».

Плитка ссылается на строку той же группы в «Рекордах по возрастным группам»,
где под спойлером лежит топ-5, — поэтому тесты проверяют не только цифры по
отдельности, но и что они сходятся между собой.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult, User
from app.services.location_page_service import (
    _age_group_records,
    _age_group_tops,
    build_location_age_group_standings,
)


def _platform(db: Session, code: str, name: str) -> Platform:
    platform = db.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=name)
        db.add(platform)
        db.flush()
    return platform


def _make_participant(db: Session, platform: Platform, suffix: str, name: str) -> Participant:
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"agegroup-{suffix}",
        display_name=name,
    )
    db.add(participant)
    db.flush()
    return participant


def _link_user(db: Session, platform: Platform, participant: Participant) -> User:
    user = User(telegram_id=int(uuid4().int % 1_000_000_000), display_name=participant.display_name)
    db.add(user)
    db.flush()
    db.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url=f"https://5verst.ru/userstats/{participant.external_user_id}/",
        )
    )
    return user


def _make_location(db: Session, platform: Platform, suffix: str, name: str) -> Location:
    location = Location(
        platform_id=platform.id,
        external_key=f"agegroup-{suffix}",
        name=name,
        city="Москва",
    )
    db.add(location)
    db.flush()
    return location


def _make_event(db: Session, platform: Platform, location: Location, suffix: str, day: date) -> Event:
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"agegroup-event-{suffix}",
        event_date=day,
        event_number=int(uuid4().int % 100_000) + 900_000,
        title=location.name,
    )
    db.add(event)
    db.flush()
    return event


def _add_result(
    db: Session,
    event: Event,
    participant: Participant,
    *,
    finish_time_sec: int | None,
    age_category: str | None,
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


def test_place_is_counted_within_each_age_group(db_session: Session) -> None:
    suffix = uuid4().hex[:8]
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    location = _make_location(db_session, five_verst, f"loc-{suffix}", "Возрастной парк")
    events = [
        _make_event(db_session, five_verst, location, f"{suffix}-{index}", date(2024, 1, 6 + index * 7))
        for index in range(3)
    ]

    me = _make_participant(db_session, five_verst, f"me-{suffix}", "Я Бегун")
    user = _link_user(db_session, five_verst, me)
    faster = _make_participant(db_session, five_verst, f"fast-{suffix}", "Быстрый Сосед")
    slower = _make_participant(db_session, five_verst, f"slow-{suffix}", "Медленный Сосед")

    # Первый год — группа «30–34»: я второй из троих (1500 против 1400 и 1600).
    _add_result(db_session, events[0], me, finish_time_sec=1500, age_category="М30-34")
    _add_result(db_session, events[0], faster, finish_time_sec=1400, age_category="М30-34")
    _add_result(db_session, events[0], slower, finish_time_sec=1600, age_category="М30-34")
    # Второй старт в той же группе — личный минимум группы становится 1450.
    _add_result(db_session, events[1], me, finish_time_sec=1450, age_category="М30-34")

    # Перешёл в «35–39»: здесь я первый, хотя время (1550) хуже, чем у соседа в
    # прошлой группе, — сравнение идёт только внутри своей категории.
    _add_result(db_session, events[2], me, finish_time_sec=1550, age_category="М35-39")
    _add_result(db_session, events[2], slower, finish_time_sec=1700, age_category="М35-39")

    db_session.commit()
    event_ids = [event.id for event in events]

    standings = build_location_age_group_standings(db_session, user.id, event_ids)
    assert [item["age_group"] for item in standings] == ["35–39", "30–34"]

    current, previous = standings
    assert (current["key"], current["label"]) == ("male-35-39", "М35–39")
    assert (current["place"], current["total"], current["runs_count"]) == (1, 2, 1)
    assert current["best_time_sec"] == 1550

    assert (previous["key"], previous["label"]) == ("male-30-34", "М30–34")
    assert (previous["place"], previous["total"], previous["runs_count"]) == (2, 3, 2)
    assert previous["best_time_sec"] == 1450
    assert previous["best_time_date"] == date(2024, 1, 13)

    top = _age_group_tops(db_session, event_ids)[("male", "30–34")]
    assert [(row["place"], row["name"], row["best_time_display"]) for row in top] == [
        (1, "Быстрый Сосед", "00:23:20"),
        (2, "Я Бегун", "00:24:10"),
        (3, "Медленный Сосед", "00:26:40"),
    ]


def test_tile_place_matches_the_top_it_links_to(db_session: Session) -> None:
    """«#N» на плитке и позиция в топ-5 той же группы — одно и то же число."""
    suffix = uuid4().hex[:8]
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    location = _make_location(db_session, five_verst, f"match-{suffix}", "Парк сходимости")
    event = _make_event(db_session, five_verst, location, f"match-{suffix}", date(2024, 2, 3))

    me = _make_participant(db_session, five_verst, f"match-me-{suffix}", "Я Бегун")
    user = _link_user(db_session, five_verst, me)
    _add_result(db_session, event, me, finish_time_sec=1500, age_category="М40-44")
    for index, seconds in enumerate((1200, 1300, 1400)):
        rival = _make_participant(db_session, five_verst, f"match-r{index}-{suffix}", f"Соперник {index}")
        _add_result(db_session, event, rival, finish_time_sec=seconds, age_category="М40-44")
    db_session.commit()

    standing = build_location_age_group_standings(db_session, user.id, [event.id])[0]
    top = _age_group_tops(db_session, [event.id])[("male", "40–44")]
    my_row = next(row for row in top if row["best_time_sec"] == standing["best_time_sec"])
    assert standing["place"] == my_row["place"] == 4

    # Первая строка топа обязана совпадать с рекордом группы в той же таблице:
    # плитка ведёт в эту таблицу, расхождение читалось бы как ошибка счёта.
    record = next(r for r in _age_group_records(db_session, [event.id]) if r["key"] == standing["key"])
    assert record["finish_time_sec"] == top[0]["best_time_sec"]
    assert record["top"] == top


def test_runpark_and_five_verst_land_in_one_group(db_session: Session) -> None:
    """«М30-34» 5 вёрст и «SM30-34» RunPark — одна группа «30–34», один топ."""
    suffix = uuid4().hex[:8]
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    runpark = _platform(db_session, "runpark", "RunPark")
    fv_location = _make_location(db_session, five_verst, f"mix-fv-{suffix}", "Парк смешанный")
    rp_location = _make_location(db_session, runpark, f"mix-rp-{suffix}", "Парк смешанный")
    fv_event = _make_event(db_session, five_verst, fv_location, f"mix-fv-{suffix}", date(2024, 5, 4))
    rp_event = _make_event(db_session, runpark, rp_location, f"mix-rp-{suffix}", date(2024, 5, 11))

    me = _make_participant(db_session, five_verst, f"mix-me-{suffix}", "Я Бегун")
    user = _link_user(db_session, five_verst, me)
    rp_runner = _make_participant(db_session, runpark, f"mix-rp-runner-{suffix}", "Бегун РанПарка")

    _add_result(db_session, fv_event, me, finish_time_sec=1500, age_category="М30-34")
    _add_result(db_session, rp_event, rp_runner, finish_time_sec=1300, age_category="SM30-34")
    db_session.commit()

    event_ids = [fv_event.id, rp_event.id]
    standings = build_location_age_group_standings(db_session, user.id, event_ids)
    assert len(standings) == 1
    assert (standings[0]["age_group"], standings[0]["place"], standings[0]["total"]) == ("30–34", 2, 2)

    top = _age_group_tops(db_session, event_ids)[("male", "30–34")]
    assert [row["name"] for row in top] == ["Бегун РанПарка", "Я Бегун"]


def test_parkrun_age_grade_is_not_an_age_group(db_session: Session) -> None:
    """В age_category у parkrun лежит age grade («54.38%») — в группы не идёт."""
    suffix = uuid4().hex[:8]
    parkrun = _platform(db_session, "parkrun", "parkrun")
    location = _make_location(db_session, parkrun, f"pr-{suffix}", "Парк паркрана")
    event = _make_event(db_session, parkrun, location, f"pr-{suffix}", date(2019, 6, 1))

    me = _make_participant(db_session, parkrun, f"pr-me-{suffix}", "Я Бегун")
    user = _link_user(db_session, parkrun, me)
    _add_result(db_session, event, me, finish_time_sec=1500, age_category="54.38%")
    db_session.commit()

    assert build_location_age_group_standings(db_session, user.id, [event.id]) == []
    assert _age_group_tops(db_session, [event.id]) == {}


def test_results_without_time_or_category_are_skipped(db_session: Session) -> None:
    suffix = uuid4().hex[:8]
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    location = _make_location(db_session, five_verst, f"skip-{suffix}", "Парк без категорий")
    event = _make_event(db_session, five_verst, location, f"skip-{suffix}", date(2024, 3, 2))

    me = _make_participant(db_session, five_verst, f"skip-me-{suffix}", "Я Бегун")
    user = _link_user(db_session, five_verst, me)
    _add_result(db_session, event, me, finish_time_sec=1500, age_category=None)
    db_session.commit()
    assert build_location_age_group_standings(db_session, user.id, [event.id]) == []

    other = _make_event(db_session, five_verst, location, f"skip2-{suffix}", date(2024, 3, 9))
    # Незачётная строка: категория есть, времени нет.
    _add_result(db_session, other, me, finish_time_sec=None, age_category="М30-34")
    db_session.commit()
    assert build_location_age_group_standings(db_session, user.id, [event.id, other.id]) == []


def test_equal_best_times_share_one_place(db_session: Session) -> None:
    """Одинаковое лучшее время — одно место на двоих (спортивный ранг)."""
    suffix = uuid4().hex[:8]
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    location = _make_location(db_session, five_verst, f"tie-{suffix}", "Парк ничьих")
    event = _make_event(db_session, five_verst, location, f"tie-{suffix}", date(2024, 4, 6))

    me = _make_participant(db_session, five_verst, f"tie-me-{suffix}", "Я Бегун")
    user = _link_user(db_session, five_verst, me)
    twin = _make_participant(db_session, five_verst, f"tie-twin-{suffix}", "Ровно Такой Же")
    leader = _make_participant(db_session, five_verst, f"tie-lead-{suffix}", "Лидер Группы")

    _add_result(db_session, event, leader, finish_time_sec=1200, age_category="М40-44")
    _add_result(db_session, event, me, finish_time_sec=1500, age_category="М40-44")
    _add_result(db_session, event, twin, finish_time_sec=1500, age_category="М40-44")
    db_session.commit()

    standings = build_location_age_group_standings(db_session, user.id, [event.id])
    assert len(standings) == 1
    assert (standings[0]["place"], standings[0]["total"]) == (2, 3)
    top = _age_group_tops(db_session, [event.id])[("male", "40–44")]
    assert [row["place"] for row in top] == [1, 2, 2]
