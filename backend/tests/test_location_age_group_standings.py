"""Топ локации по возрастным группам для блока «Вы на этой локации».

Смысл фичи: у участника столько плиток, сколько возрастных категорий он успел
пройти на этой площадке, и в каждой — своё место среди тех, кто бежал здесь в
той же категории. Сравнение всегда внутри группы: лучшее время участника в
«М30-34» не конкурирует с временами «М35-39».
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult, User
from app.services.location_page_service import build_location_age_group_standings


def _five_verst_platform(db: Session) -> Platform:
    platform = db.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        platform = Platform(code="five_verst", name="5 вёрст")
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
            finish_time_display=None,
            age_category=age_category,
            status="finished",
        )
    )


def test_age_group_standings_rank_within_each_group(db_session: Session) -> None:
    suffix = uuid4().hex[:8]
    platform = _five_verst_platform(db_session)

    location = Location(
        platform_id=platform.id,
        external_key=f"agegroup-loc-{suffix}",
        name="Возрастной парк",
        city="Москва",
    )
    db_session.add(location)
    db_session.flush()

    events = []
    for index in range(3):
        event = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"agegroup-event-{suffix}-{index}",
            event_date=date(2024, 1, 6 + index * 7),
            event_number=910_000 + index,
            title="Возрастной парк",
        )
        db_session.add(event)
        events.append(event)
    db_session.flush()

    me = _make_participant(db_session, platform, f"me-{suffix}", "Я Бегун")
    user = User(telegram_id=int(uuid4().int % 1_000_000_000), display_name="Я Бегун")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=me.id,
            external_user_id=me.external_user_id,
            external_url=f"https://5verst.ru/userstats/{me.external_user_id}/",
        )
    )

    faster = _make_participant(db_session, platform, f"fast-{suffix}", "Быстрый Сосед")
    slower = _make_participant(db_session, platform, f"slow-{suffix}", "Медленный Сосед")

    # Первый год — группа «М30-34»: я вторым из троих (1500 против 1400 и 1600).
    _add_result(db_session, events[0], me, finish_time_sec=1500, age_category="М30-34")
    _add_result(db_session, events[0], faster, finish_time_sec=1400, age_category="М30-34")
    _add_result(db_session, events[0], slower, finish_time_sec=1600, age_category="М30-34")
    # Второй старт в той же группе — личный минимум группы становится 1450.
    _add_result(db_session, events[1], me, finish_time_sec=1450, age_category="М30-34")

    # Перешёл в «М35-39»: здесь я первый, хотя время (1550) хуже, чем у соседа
    # в прошлой группе, — сравнение идёт только внутри своей категории.
    _add_result(db_session, events[2], me, finish_time_sec=1550, age_category="М35-39")
    _add_result(db_session, events[2], slower, finish_time_sec=1700, age_category="М35-39")

    db_session.commit()

    standings = build_location_age_group_standings(
        db_session, user.id, [event.id for event in events]
    )

    assert [item["age_category"] for item in standings] == ["М35-39", "М30-34"]

    current, previous = standings
    assert current["place"] == 1
    assert current["total"] == 2
    assert current["runs_count"] == 1
    assert current["best_time_sec"] == 1550
    assert current["label"] == "М35–39"

    assert previous["place"] == 2
    assert previous["total"] == 3
    assert previous["runs_count"] == 2
    assert previous["best_time_sec"] == 1450
    assert previous["best_time_date"] == date(2024, 1, 13)

    top = previous["top"]
    assert [row["place"] for row in top] == [1, 2, 3]
    assert [row["name"] for row in top] == ["Быстрый Сосед", "Я Бегун", "Медленный Сосед"]
    assert [row["is_me"] for row in top] == [False, True, False]
    assert top[1]["best_time_display"] == "00:24:10"


def test_age_group_standings_ignores_foreign_and_missing_categories(db_session: Session) -> None:
    """В группы попадают только категории 5 вёрст с известным временем."""
    suffix = uuid4().hex[:8]
    platform = _five_verst_platform(db_session)

    location = Location(
        platform_id=platform.id,
        external_key=f"agegroup-skip-{suffix}",
        name="Парк без категорий",
        city="Москва",
    )
    db_session.add(location)
    db_session.flush()

    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"agegroup-skip-event-{suffix}",
        event_date=date(2024, 3, 2),
        event_number=915_000,
        title="Парк без категорий",
    )
    db_session.add(event)
    db_session.flush()

    me = _make_participant(db_session, platform, f"skip-{suffix}", "Я Бегун")
    user = User(telegram_id=int(uuid4().int % 1_000_000_000), display_name="Я Бегун")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=me.id,
            external_user_id=me.external_user_id,
            external_url=f"https://5verst.ru/userstats/{me.external_user_id}/",
        )
    )

    _add_result(db_session, event, me, finish_time_sec=1500, age_category=None)
    db_session.commit()
    assert build_location_age_group_standings(db_session, user.id, [event.id]) == []

    other_event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"agegroup-skip-event2-{suffix}",
        event_date=date(2024, 3, 9),
        event_number=915_001,
        title="Парк без категорий",
    )
    db_session.add(other_event)
    db_session.flush()
    # Код parkrun/runpark и незачётная строка без времени — обе мимо.
    _add_result(db_session, other_event, me, finish_time_sec=1500, age_category="SM30-34")
    db_session.commit()
    assert build_location_age_group_standings(db_session, user.id, [event.id, other_event.id]) == []


def test_age_group_standings_shares_place_on_equal_times(db_session: Session) -> None:
    """Одинаковое лучшее время — одно место на двоих (спортивный ранг)."""
    suffix = uuid4().hex[:8]
    platform = _five_verst_platform(db_session)

    location = Location(
        platform_id=platform.id,
        external_key=f"agegroup-tie-{suffix}",
        name="Парк ничьих",
        city="Москва",
    )
    db_session.add(location)
    db_session.flush()

    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"agegroup-tie-event-{suffix}",
        event_date=date(2024, 4, 6),
        event_number=917_000,
        title="Парк ничьих",
    )
    db_session.add(event)
    db_session.flush()

    me = _make_participant(db_session, platform, f"tie-me-{suffix}", "Я Бегун")
    user = User(telegram_id=int(uuid4().int % 1_000_000_000), display_name="Я Бегун")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=me.id,
            external_user_id=me.external_user_id,
            external_url=f"https://5verst.ru/userstats/{me.external_user_id}/",
        )
    )
    twin = _make_participant(db_session, platform, f"tie-twin-{suffix}", "Ровно Такой Же")
    leader = _make_participant(db_session, platform, f"tie-lead-{suffix}", "Лидер Группы")

    _add_result(db_session, event, leader, finish_time_sec=1200, age_category="М40-44")
    _add_result(db_session, event, me, finish_time_sec=1500, age_category="М40-44")
    _add_result(db_session, event, twin, finish_time_sec=1500, age_category="М40-44")
    db_session.commit()

    standings = build_location_age_group_standings(db_session, user.id, [event.id])
    assert len(standings) == 1
    assert standings[0]["place"] == 2
    assert standings[0]["total"] == 3
    assert [row["place"] for row in standings[0]["top"]] == [1, 2, 2]
