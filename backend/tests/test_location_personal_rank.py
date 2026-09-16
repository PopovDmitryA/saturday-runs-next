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
    db_session.commit()

    stats = build_location_personal_stats(db_session, user, location.external_key)
    assert stats is not None
    assert stats["runs_count"] == 2
    # Впереди только лидер с тремя стартами; безымянный со всеми четырьмя — мимо.
    assert stats["rank_by_runs"] == 2
    # В знаменателе живые люди: я, лидер и редкий гость.
    assert stats["runners_total"] == 3
