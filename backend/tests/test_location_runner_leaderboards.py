"""Топы страницы локации: по лучшему времени и по числу побед.

Двадцатка по времени — одна строка на человека (его лучший результат здесь),
десятка по победам — сколько раз человек финишировал первым в абсолюте и
среди женщин.

Отдельно закреплено поведение вокруг безымянных строк протокола: в топах их
нет, но победу они забирают. Если быстрее всех на старте пробежал участник без
штрихкода, победы в тот день не было ни у кого — иначе «победителем» стал бы
второй на финише, чего в протоколе не было.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult, User
from app.services.location_page_service import (
    _compute_location_leaders,
    _fastest_runners,
    _top_winners,
    build_location_tops,
)

FIRST_DATE = date(2026, 5, 2)


def _platform(db_session: Session) -> Platform:
    return db_session.query(Platform).filter(Platform.code == "five_verst").one()


@pytest.fixture
def location(db_session: Session) -> Location:
    row = Location(
        platform_id=_platform(db_session).id,
        external_key=f"leaders-{uuid4().hex[:8]}",
        name="Площадка топов",
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def events(db_session: Session, location: Location) -> list[Event]:
    rows = [
        Event(
            platform_id=location.platform_id,
            location_id=location.id,
            external_event_key=f"leaders-event-{uuid4().hex[:8]}",
            event_date=FIRST_DATE + timedelta(days=7 * index),
        )
        for index in range(2)
    ]
    db_session.add_all(rows)
    db_session.flush()
    return rows


def _runner(db_session: Session, name: str) -> Participant:
    row = Participant(
        platform_id=_platform(db_session).id,
        external_user_id=f"79{uuid4().int % 10**9:09d}",
        display_name=name,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _finish(
    db_session: Session,
    event: Event,
    runner: Participant,
    *,
    seconds: int,
    age_category: str = "М30-34",
) -> RunResult:
    row = RunResult(
        event_id=event.id,
        participant_id=runner.id,
        external_result_key=f"{event.external_event_key}:{uuid4().hex[:8]}",
        finish_time_sec=seconds,
        age_category=age_category,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_fastest_keeps_one_row_per_runner(db_session: Session, events: list[Event]) -> None:
    """Два результата одного человека — одна строка с лучшим временем и его датой."""
    swift = _runner(db_session, "Борис БЫСТРЫЙ")
    steady = _runner(db_session, "Артём РОВНЫЙ")
    _finish(db_session, events[0], swift, seconds=1200)
    _finish(db_session, events[1], swift, seconds=1140)
    _finish(db_session, events[0], steady, seconds=1110)

    rows = _fastest_runners(db_session, [event.id for event in events], "male", limit=20)

    assert [(row["name"], row["best_time_sec"], row["place"]) for row in rows] == [
        ("Артём РОВНЫЙ", 1110, 1),
        ("Борис БЫСТРЫЙ", 1140, 2),
    ]
    assert rows[1]["event_date"] == events[1].event_date
    assert rows[1]["platform_codes"] == ["five_verst"]


def test_fastest_splits_by_gender(db_session: Session, events: list[Event]) -> None:
    man = _runner(db_session, "Борис БЫСТРЫЙ")
    woman = _runner(db_session, "Вера БЫСТРАЯ")
    _finish(db_session, events[0], man, seconds=1140)
    _finish(db_session, events[0], woman, seconds=1320, age_category="Ж30-34")
    event_ids = [event.id for event in events]

    assert [row["name"] for row in _fastest_runners(db_session, event_ids, "male", limit=20)] == [
        "Борис БЫСТРЫЙ"
    ]
    assert [row["name"] for row in _fastest_runners(db_session, event_ids, "female", limit=20)] == [
        "Вера БЫСТРАЯ"
    ]


def test_fastest_skips_unnamed_protocol_rows(db_session: Session, events: list[Event]) -> None:
    """Финишёр без штрихбкода в топ не попадает — это заглушка, а не человек."""
    unknown = _runner(db_session, "НЕИЗВЕСТНЫЙ")
    named = _runner(db_session, "Борис БЫСТРЫЙ")
    _finish(db_session, events[0], unknown, seconds=1020)
    _finish(db_session, events[0], named, seconds=1140)

    rows = _fastest_runners(db_session, [event.id for event in events], "male", limit=20)

    assert [row["name"] for row in rows] == ["Борис БЫСТРЫЙ"]


def test_fastest_respects_limit(db_session: Session, events: list[Event]) -> None:
    for index in range(4):
        _finish(db_session, events[0], _runner(db_session, f"Бегун {index}"), seconds=1200 + index)

    rows = _fastest_runners(db_session, [event.id for event in events], "male", limit=2)

    assert [row["place"] for row in rows] == [1, 2]


def test_wins_count_first_finishes(db_session: Session, events: list[Event]) -> None:
    swift = _runner(db_session, "Борис БЫСТРЫЙ")
    steady = _runner(db_session, "Артём РОВНЫЙ")
    _finish(db_session, events[0], swift, seconds=1140)
    _finish(db_session, events[0], steady, seconds=1200)
    _finish(db_session, events[1], swift, seconds=1150)
    _finish(db_session, events[1], steady, seconds=1210)

    rows = _top_winners(db_session, [event.id for event in events], gender=None, limit=10)

    assert [(row["name"], row["wins_count"], row["place"]) for row in rows] == [
        ("Борис БЫСТРЫЙ", 2, 1)
    ]
    assert rows[0]["last_win_date"] == events[1].event_date


def test_unnamed_winner_leaves_the_start_without_a_winner(
    db_session: Session, events: list[Event]
) -> None:
    unknown = _runner(db_session, "Неизвестный бегун")
    named = _runner(db_session, "Борис БЫСТРЫЙ")
    _finish(db_session, events[0], unknown, seconds=1020)
    _finish(db_session, events[0], named, seconds=1140)
    _finish(db_session, events[1], named, seconds=1150)

    rows = _top_winners(db_session, [event.id for event in events], gender=None, limit=10)

    assert [(row["name"], row["wins_count"]) for row in rows] == [("Борис БЫСТРЫЙ", 1)]


def test_equal_time_gives_both_runners_a_win(db_session: Session, events: list[Event]) -> None:
    first = _runner(db_session, "Борис БЫСТРЫЙ")
    second = _runner(db_session, "Артём РОВНЫЙ")
    _finish(db_session, events[0], first, seconds=1140)
    _finish(db_session, events[0], second, seconds=1140)

    rows = _top_winners(db_session, [event.id for event in events], gender=None, limit=10)

    assert sorted((str(row["name"]), row["wins_count"], row["place"]) for row in rows) == [
        ("Артём РОВНЫЙ", 1, 1),
        ("Борис БЫСТРЫЙ", 1, 1),
    ]


def test_female_wins_are_counted_among_women_only(db_session: Session, events: list[Event]) -> None:
    """Женский зачёт не зависит от того, кто выиграл абсолют."""
    man = _runner(db_session, "Борис БЫСТРЫЙ")
    woman = _runner(db_session, "Вера БЫСТРАЯ")
    _finish(db_session, events[0], man, seconds=1140)
    _finish(db_session, events[0], woman, seconds=1320, age_category="Ж30-34")
    event_ids = [event.id for event in events]

    assert [(row["name"], row["wins_count"]) for row in _top_winners(
        db_session, event_ids, gender="female", limit=10
    )] == [("Вера БЫСТРАЯ", 1)]
    assert [row["name"] for row in _top_winners(db_session, event_ids, gender=None, limit=10)] == [
        "Борис БЫСТРЫЙ"
    ]


def test_empty_location_gives_empty_tops(db_session: Session) -> None:
    assert _fastest_runners(db_session, [], "male", limit=20) == []
    assert _top_winners(db_session, [], gender=None, limit=10) == []


def test_accounts_of_one_person_stay_separate_rows(
    db_session: Session, events: list[Event]
) -> None:
    """Аккаунты в разных системах по имени НЕ склеиваются — каждый своей строкой.

    Это разные внешние аккаунты: пока человек не привязал их к профилю на
    сайте, доказать, что за двумя «Александрами МЕДВЕДЕВЫМИ» один человек, мы
    не можем — в Тамбове их и правда двое. Поэтому строки остаются раздельными,
    а различает их колонка «Система» (решение Дмитрия 14.09.2026).
    """
    parkrun = db_session.query(Platform).filter(Platform.code == "parkrun").one()
    parkrun_account = Participant(
        platform_id=parkrun.id,
        external_user_id=str(uuid4().int % 10**7),
        display_name="Александр МЕДВЕДЕВ",
        age_category="SM30-34",
    )
    db_session.add(parkrun_account)
    db_session.flush()
    five_verst_account = _runner(db_session, "Александр МЕДВЕДЕВ")
    # parkrun-эра площадки: своё событие своей платформы на той же локации.
    parkrun_event = Event(
        platform_id=parkrun.id,
        location_id=events[0].location_id,
        external_event_key=f"leaders-parkrun-{uuid4().hex[:8]}",
        event_date=FIRST_DATE - timedelta(days=7),
    )
    db_session.add(parkrun_event)
    db_session.flush()
    db_session.add(
        RunResult(
            event_id=parkrun_event.id,
            participant_id=parkrun_account.id,
            external_result_key=f"{parkrun_event.external_event_key}:1",
            finish_time_sec=1140,
        )
    )
    _finish(db_session, events[0], five_verst_account, seconds=1200)
    event_ids = [event.id for event in (*events, parkrun_event)]

    fastest = _fastest_runners(db_session, event_ids, "male", limit=20)
    wins = _top_winners(db_session, event_ids, gender=None, limit=10)

    assert [(row["best_time_sec"], row["platform_codes"]) for row in fastest] == [
        (1140, ["parkrun"]),
        (1200, ["five_verst"]),
    ]
    assert [(row["wins_count"], row["platform_codes"]) for row in wins] == [
        (1, ["five_verst"]),
        (1, ["parkrun"]),
    ]


def test_foreign_parkrun_location_has_no_tops(db_session: Session) -> None:
    """По зарубежному parkrun топов нет: его протоколы мы не собираем.

    В БД от такой площадки лежат только результаты наших же участников из их
    профилей — «топ по победам» там был бы выдумкой.
    """
    parkrun = db_session.query(Platform).filter(Platform.code == "parkrun").one()
    location = Location(
        platform_id=parkrun.id,
        external_key=f"leaders-foreign-{uuid4().hex[:8]}",
        name="Bushy Park",
        country="United Kingdom",
    )
    db_session.add(location)
    db_session.flush()
    event = Event(
        platform_id=parkrun.id,
        location_id=location.id,
        external_event_key=f"leaders-foreign-event-{uuid4().hex[:8]}",
        event_date=FIRST_DATE,
    )
    db_session.add(event)
    db_session.flush()
    tourist = Participant(
        platform_id=parkrun.id,
        external_user_id=str(uuid4().int % 10**7),
        display_name="Дмитрий ТУРИСТ",
        age_category="SM30-34",
    )
    db_session.add(tourist)
    db_session.flush()
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=tourist.id,
            external_result_key=f"{event.external_event_key}:1",
            finish_time_sec=1500,
        )
    )
    db_session.flush()

    payload = _compute_location_leaders(db_session, location.external_key)

    assert payload is not None
    assert payload["fastest_male"] == []
    assert payload["winners_overall"] == []
    # А в топе по пробежкам эта строка по-прежнему есть: он про «кто сюда
    # ходит», а не про «кто здесь первый», и зарубежных площадок не стесняется.
    assert [row["name"] for row in cast(list[Any], payload["runners"])] == ["Дмитрий ТУРИСТ"]


def test_fastest_counts_finishes_here(db_session: Session, events: list[Event]) -> None:
    """Рядом с лучшим временем — сколько раз человек здесь финишировал."""
    regular = _runner(db_session, "Артём РОВНЫЙ")
    tourist = _runner(db_session, "Борис ЗАЕЗЖИЙ")
    _finish(db_session, events[0], regular, seconds=1200)
    _finish(db_session, events[1], regular, seconds=1140)
    _finish(db_session, events[0], tourist, seconds=1110)

    rows = _fastest_runners(db_session, [event.id for event in events], "male", limit=None)

    assert [(row["name"], row["finishes_count"]) for row in rows] == [
        ("Борис ЗАЕЗЖИЙ", 1),
        ("Артём РОВНЫЙ", 2),
    ]


def test_wins_carry_first_and_last_dates(db_session: Session, events: list[Event]) -> None:
    winner = _runner(db_session, "Борис БЫСТРЫЙ")
    _finish(db_session, events[0], winner, seconds=1140)
    _finish(db_session, events[1], winner, seconds=1150)

    rows = _top_winners(db_session, [event.id for event in events], gender=None, limit=None)

    assert rows[0]["first_win_date"] == events[0].event_date
    assert rows[0]["last_win_date"] == events[1].event_date


def test_build_location_tops_returns_full_scopes(
    db_session: Session, location: Location, events: list[Event]
) -> None:
    """Витрина «Топы бегунов» отдаёт зачёты целиком, без лимита."""
    for index in range(25):
        _finish(db_session, events[0], _runner(db_session, f"Бегун {index:02d}"), seconds=1200 + index)
    woman = _runner(db_session, "Вера БЫСТРАЯ")
    _finish(db_session, events[1], woman, seconds=1320, age_category="Ж30-34")

    payload = build_location_tops(db_session, location.external_key, use_cache=False)

    assert payload is not None
    # Двадцатка и десятка — это лимиты превью на странице локации; здесь их нет.
    assert len(cast(list[Any], payload["fastest_male"])) == 25
    assert [row["name"] for row in cast(list[Any], payload["fastest_female"])] == ["Вера БЫСТРАЯ"]
    assert payload["platform_codes"] == ["five_verst"]
    # Победителей двое: по одному на старт (во второй старт бежала только она).
    assert len(cast(list[Any], payload["winners_overall"])) == 2


def test_build_location_tops_skips_foreign_parkrun(db_session: Session) -> None:
    """Зарубежный parkrun не даёт зачётов и на полной витрине."""
    parkrun = db_session.query(Platform).filter(Platform.code == "parkrun").one()
    location = Location(
        platform_id=parkrun.id,
        external_key=f"tops-foreign-{uuid4().hex[:8]}",
        name="Bushy Park",
        country="United Kingdom",
    )
    db_session.add(location)
    db_session.flush()

    payload = build_location_tops(db_session, location.external_key, use_cache=False)

    assert payload is not None
    assert payload["fastest_male"] == []
    assert payload["winners_overall"] == []
    assert payload["platform_codes"] == []


def test_linked_profile_shows_every_system_it_ran_in(
    db_session: Session, location: Location, events: list[Event]
) -> None:
    """У профиля сайта со связанными аккаунтами в колонке стоят ВСЕ его системы.

    Лучшее время выбрано из результатов обеих систем площадки, значит и
    считались обе — показывать только ту, где оказался рекорд, неправильно
    (правка Дмитрия 14.09.2026).
    """
    parkrun = db_session.query(Platform).filter(Platform.code == "parkrun").one()
    five_verst_account = _runner(db_session, "Оксана СВЯЗАННАЯ")
    parkrun_account = Participant(
        platform_id=parkrun.id,
        external_user_id=str(uuid4().int % 10**7),
        display_name="Оксана СВЯЗАННАЯ",
        age_category="SW30-34",
    )
    db_session.add(parkrun_account)
    db_session.flush()
    # Единый профиль на сайте: оба аккаунта привязаны к одному пользователю.
    user = User(
        telegram_id=uuid4().int % 10**9,
        display_name="Оксана Связанная",
    )
    db_session.add(user)
    db_session.flush()
    for account in (five_verst_account, parkrun_account):
        db_session.add(
            PlatformLink(
                user_id=user.id,
                platform_id=account.platform_id,
                participant_id=account.id,
                external_user_id=account.external_user_id,
                external_url=f"https://example.test/{account.external_user_id}",
            )
        )
    parkrun_event = Event(
        platform_id=parkrun.id,
        location_id=location.id,
        external_event_key=f"leaders-parkrun-{uuid4().hex[:8]}",
        event_date=FIRST_DATE - timedelta(days=7),
    )
    db_session.add(parkrun_event)
    db_session.flush()
    db_session.add(
        RunResult(
            event_id=parkrun_event.id,
            participant_id=parkrun_account.id,
            external_result_key=f"{parkrun_event.external_event_key}:1",
            finish_time_sec=1320,
        )
    )
    _finish(db_session, events[0], five_verst_account, seconds=1290, age_category="Ж30-34")
    event_ids = [event.id for event in (*events, parkrun_event)]

    rows = _fastest_runners(db_session, event_ids, "female", limit=None)

    # Одна строка на человека: аккаунты связаны профилем сайта.
    assert len(rows) == 1
    assert rows[0]["best_time_sec"] == 1290
    assert rows[0]["finishes_count"] == 2
    assert rows[0]["platform_codes"] == ["five_verst", "parkrun"]
