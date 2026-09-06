"""Обход протоколов недели: окно субботы и отбор кандидатов.

Третья страховка легаси-схемы. Сводка знает только число финишёров, число
волонтёров и три времени — правку внутри протокола (роль волонтёра, привязка
к атлету, имя, позиция) не видит ни сверка, ни ротация: обе сравнивают наш
протокол с НАШЕЙ ЖЕ сводкой. Здесь протоколы субботы перекачиваются целиком.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, EventSummary, Location, Platform, ProtocolSyncState
from app.sync.five_verst_week_sweep import WeekSweepOptions, plan_week_sweep, week_window


@pytest.mark.parametrize(
    ("today", "weeks_back", "expected_start"),
    [
        # Понедельник смотрит позавчерашнюю субботу.
        (date(2026, 9, 7), 0, date(2026, 9, 5)),
        # Четверг — та же суббота: анкер один на всю неделю.
        (date(2026, 9, 10), 0, date(2026, 9, 5)),
        # Пятница — W−2, то есть суббота двумя неделями раньше.
        (date(2026, 9, 11), 2, date(2026, 8, 22)),
        # Среда — W−1.
        (date(2026, 9, 9), 1, date(2026, 8, 29)),
        # В саму субботу «последняя суббота» — сегодняшняя.
        (date(2026, 9, 5), 0, date(2026, 9, 5)),
    ],
)
def test_week_window_anchors_on_saturday(
    today: date, weeks_back: int, expected_start: date
) -> None:
    start, end = week_window(weeks_back, today=today)
    assert start == expected_start
    # Окно — суббота плюс шесть дней: перенос старта (1 января, спецзабег)
    # попадает в ту же неделю, что и суббота, за которую он идёт.
    assert end == expected_start + timedelta(days=6)


def _platform(db: Session) -> Platform:
    platform = db.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        platform = Platform(code="five_verst", name="5 вёрст")
        db.add(platform)
        db.flush()
    return platform


def _event_with_summary(
    db: Session,
    platform: Platform,
    location: Location,
    day: date,
    *,
    fetched_at: datetime | None,
) -> str:
    key = f"sweep-{uuid4().hex[:10]}"
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=key,
        event_date=day,
        title=location.name,
    )
    db.add(event)
    db.flush()
    db.add(
        EventSummary(
            platform_id=platform.id,
            location_id=location.id,
            event_id=event.id,
            external_event_key=key,
            event_date=day,
            summary_hash=f"hash-{key}",
        )
    )
    if fetched_at is not None:
        db.add(
            ProtocolSyncState(
                event_id=event.id,
                last_protocol_check_at=fetched_at,
                last_protocol_fetched_at=fetched_at,
            )
        )
    db.flush()
    return key


def _location(db: Session, platform: Platform, name: str) -> Location:
    location = Location(
        platform_id=platform.id,
        external_key=f"sweep-loc-{uuid4().hex[:8]}",
        name=name,
    )
    db.add(location)
    db.flush()
    return location


def test_plan_takes_the_week_and_skips_just_fetched(db_session: Session) -> None:
    """Свежескачанный протокол пропускаем, чужую неделю не берём вовсе.

    Пропуск нужен, чтобы звенья цепочки двигались вперёд: без него первый же
    заход перекачивал бы одни и те же 60 протоколов по кругу.
    """
    platform = _platform(db_session)
    # Каждой площадке — своя строка: (платформа, локация, дата) уникальны.
    now = datetime.now(timezone.utc)
    stale = _event_with_summary(
        db_session,
        platform,
        _location(db_session, platform, "Давняя"),
        date(2026, 9, 5),
        fetched_at=now - timedelta(days=3),
    )
    never = _event_with_summary(
        db_session,
        platform,
        _location(db_session, platform, "Ни разу"),
        date(2026, 9, 5),
        fetched_at=None,
    )
    _just_fetched = _event_with_summary(
        db_session,
        platform,
        _location(db_session, platform, "Только что"),
        date(2026, 9, 5),
        fetched_at=now - timedelta(hours=2),
    )
    _other_week = _event_with_summary(
        db_session,
        platform,
        _location(db_session, platform, "Прошлая неделя"),
        date(2026, 8, 29),
        fetched_at=None,
    )

    planned = plan_week_sweep(
        db_session,
        WeekSweepOptions(weeks_back=0, limit=50, today=date(2026, 9, 7)),
    )
    keys = {summary.external_event_key for summary, _location in planned}
    assert stale in keys
    assert never in keys
    assert _just_fetched not in keys
    assert _other_week not in keys
    # Ни разу не качанный идёт первым — nullsfirst.
    assert planned[0][0].external_event_key == never
