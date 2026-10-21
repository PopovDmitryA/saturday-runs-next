"""Место участника в топе локации по числу пробежек.

Плитка на странице локации («#3 в топе по пробежкам · из 240 бегунов») считает
всех бегунов площадки без разбивки по полу (решение Дмитрия 17.09.2026), а
знаменатель берёт по тем же правилам, что таблица лидеров: привязанные аккаунты
— один человек, заглушки протокола («НЕИЗВЕСТНЫЙ») не в счёт.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult, User
from app.services.location_page_service import build_location_personal_stats


def _platform(db: Session, code: str = "five_verst", name: str = "5 вёрст") -> Platform:
    platform = db.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=name)
        db.add(platform)
        db.flush()
    return platform


def _participant(db: Session, platform: Platform, suffix: str, name: str) -> Participant:
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"locrank-{suffix}",
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
    db.flush()
    return user


def test_rank_by_runs_counts_everyone_and_skips_unnamed(db_session: Session) -> None:
    suffix = uuid4().hex[:8]
    platform = _platform(db_session)
    location = Location(
        platform_id=platform.id,
        external_key=f"locrank-{suffix}",
        name="Парк рейтинга",
        city="Москва",
    )
    db_session.add(location)
    db_session.flush()

    events = []
    for index in range(4):
        event = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"locrank-{suffix}-{index}",
            event_date=date(2097, 3, 6 + index * 7),
            event_number=930_000 + index,
            title=location.name,
        )
        db_session.add(event)
        events.append(event)
    db_session.flush()

    me = _participant(db_session, platform, f"me-{suffix}", "Я Бегун")
    user = _link_user(db_session, platform, me)
    leader = _participant(db_session, platform, f"leader-{suffix}", "Лидер Площадки")
    rare = _participant(db_session, platform, f"rare-{suffix}", "Редкий Гость")
    # Заглушка протокола: у s95 в такой аккаунт сложены все безымянные финиши,
    # и без отсечки он занимал бы верх таблицы.
    unnamed = _participant(db_session, platform, f"unknown-{suffix}", "НЕИЗВЕСТНЫЙ")
    # Строка протокола с именем, но без ID: одноразовая личность unknown: на
    # каждую строку. Не человек для знаменателя, как и у «уникальных участников»
    # (на Тимирязевском такие давали 3 780 «бегунов» против 3 694 — репорт Егора).
    one_off = Participant(
        platform_id=platform.id,
        external_user_id=f"unknown:locrank-{suffix}:2097-03-06:9",
        display_name="Сергей КЛУБОВ",
    )
    db_session.add(one_off)
    db_session.flush()

    def _run(event: Event, participant: Participant) -> None:
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"{event.external_event_key}:{participant.external_user_id}",
                finish_time_sec=1500,
                status="finished",
            )
        )

    for event in events[:2]:
        _run(event, me)
    for event in events[:3]:
        _run(event, leader)
    _run(events[0], rare)
    for event in events:
        _run(event, unnamed)
    _run(events[0], one_off)
    db_session.commit()

    stats = build_location_personal_stats(db_session, user, location.external_key)
    assert stats is not None
    assert stats["runs_count"] == 2
    # Впереди только лидер с тремя стартами; безымянный со всеми четырьмя — мимо.
    assert stats["rank_by_runs"] == 2
    # В знаменателе живые люди: я, лидер и редкий гость.
    assert stats["runners_total"] == 3


def test_named_rows_without_barcode_are_not_runners(db_session: Session) -> None:
    """Строка 5 вёрст без штрихкода, но с именем — не отдельный бегун на каждый старт.

    Йошкар-Ола, репорт 21.10.2026: «из 1013 бегунов» в плитке места, а
    «уникальных участников» 996. Разницу дали 17 строк без штрихкода: имя в
    протоколе живое, а личность у каждой строки своя (unknown:<локация>:<дата>:<место>),
    и проверка имени группы считала её человеком — Peter ZDRAV шёл за 11.
    Плитка, таблица лидеров, постоянный состав и «уникальные участники»
    обязаны давать одно число.
    """
    from app.services import location_page_service

    suffix = uuid4().hex[:8]
    platform = _platform(db_session)
    location = Location(
        platform_id=platform.id,
        external_key=f"locrank-nobarcode-{suffix}",
        name="Парк без штрихкодов",
        city="Йошкар-Ола",
    )
    db_session.add(location)
    db_session.flush()

    events = []
    for index in range(3):
        event = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"locrank-nobarcode-{suffix}-{index}",
            event_date=date(2097, 4, 3 + index * 7),
            event_number=940_000 + index,
            title=location.name,
        )
        db_session.add(event)
        events.append(event)
    db_session.flush()

    me = _participant(db_session, platform, f"nb-me-{suffix}", "Я Бегун")
    user = _link_user(db_session, platform, me)
    regular = _participant(db_session, platform, f"nb-regular-{suffix}", "Завсегдатай Парка")

    def _run(event: Event, participant: Participant, position: int) -> None:
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"{event.external_event_key}:{participant.external_user_id}",
                position=position,
                finish_time_sec=1500 + position,
                status="finished",
            )
        )

    for event in events:
        _run(event, me, 1)
        _run(event, regular, 2)
        # Человек без штрихкода: имя есть, личность на каждой строке своя.
        no_barcode = Participant(
            platform_id=platform.id,
            external_user_id=f"unknown:{location.external_key}:{event.event_date}:3",
            display_name="Peter ZDRAV",
        )
        db_session.add(no_barcode)
        db_session.flush()
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=no_barcode.id,
                external_result_key=f"{event.external_event_key}:3",
                position=3,
                finish_time_sec=1503,
                status="unknown",
            )
        )
    db_session.commit()

    stats = build_location_personal_stats(db_session, user, location.external_key)
    assert stats is not None
    assert stats["runners_total"] == 2

    page = location_page_service.build_location_page(db_session, location.external_key, use_cache=False)
    assert page is not None
    assert page["stats"]["unique_participants"] == 2

    leaders = location_page_service.build_location_leaders(db_session, location.external_key, use_cache=False)
    assert leaders is not None
    assert [row["name"] for row in leaders["runners"]] == ["Завсегдатай Парка", "Я Бегун"]

    participants = location_page_service.build_location_participants(db_session, location.external_key, use_cache=False)
    assert participants is not None
    assert participants["runners_people_total"] == 2
    assert "Peter ZDRAV" not in {row["name"] for row in participants["runners"]}
