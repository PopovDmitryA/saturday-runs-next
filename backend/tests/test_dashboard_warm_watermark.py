"""Непрерывность окон прогрева dashboard_cache.

Регрессия 08.08.2026: `since` для прогрева был моментом старта синка, то есть
окно = «пока синк работал». Промежутки между синками не покрывал никто, и
результат, записанный в такой промежуток, не сбрасывал кэш уже никогда. Так
терялись забеги S95 и RunPark — они пишутся своим расписанием, мимо окон 5 вёрст:
у 27 человек на «Обзоре» не хватало пробежек, хотя во вкладках они были.

Водяной знак делает окна встык: следующий прогон начинает там, где кончил
предыдущий, поэтому «между синками» больше не существует.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import fakeredis
from sqlalchemy.orm import Session

from app.models import (
    DashboardCache,
    Event,
    Location,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
)
from app.services.dashboard_service import _dashboard_cache_is_stale, users_with_touched_results
from app.workers.tasks.dashboard_warm import WATERMARK_KEY, _load_watermark, _save_watermark


def _platform(db: Session) -> Platform:
    platform = db.query(Platform).filter(Platform.code == "s95").one_or_none()
    if platform is None:
        platform = Platform(code="s95", name="S95")
        db.add(platform)
        db.flush()
    return platform


def _runner_with_result(db: Session, platform: Platform, *, fetched_at: datetime) -> User:
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"warm-{uuid4().hex[:8]}",
        display_name="Бегун S95",
    )
    db.add(participant)
    db.flush()
    user = User(telegram_id=int(uuid4().int % 1_000_000_000), display_name="Бегун S95")
    db.add(user)
    db.flush()
    db.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url="https://example.com",
        )
    )
    location = Location(
        platform_id=platform.id,
        external_key=f"warm-loc-{uuid4().hex[:8]}",
        name="Кусково",
        city="Москва",
    )
    db.add(location)
    db.flush()
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"warm-event-{uuid4().hex[:10]}",
        event_date=date(2026, 8, 8),
        title=location.name,
    )
    db.add(event)
    db.flush()
    db.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"{event.external_event_key}:{participant.external_user_id}",
            finish_time_sec=1084,
            status="finished",
            fetched_at=fetched_at,
        )
    )
    db.flush()
    return user


def test_result_written_between_syncs_is_still_picked_up(db_session: Session) -> None:
    """Забег S95 приехал в паузе между синками 5 вёрст — прогрев обязан его увидеть."""
    platform = _platform(db_session)
    previous_warm_finished = datetime.now(timezone.utc) - timedelta(hours=6)
    next_sync_started = datetime.now(timezone.utc) - timedelta(minutes=5)
    # Между прогревами: позже прошлого окна, раньше следующего.
    landed_at = previous_warm_finished + timedelta(hours=2)

    runner = _runner_with_result(db_session, platform, fetched_at=landed_at)

    # Как было раньше: окно от старта синка — забег в него не попадает.
    assert runner.id not in users_with_touched_results(db_session, next_sync_started)

    # Как стало: окно начинается там, где кончил прошлый прогрев.
    _save_watermark(previous_warm_finished)
    since = _load_watermark(next_sync_started)
    assert since == previous_warm_finished
    assert runner.id in users_with_touched_results(db_session, since)


def test_watermark_falls_back_to_sync_start_when_missing() -> None:
    """Redis перезапустили — прогрев не падает, а работает как до водяного знака."""
    started_at = datetime.now(timezone.utc)

    assert _load_watermark(started_at) == started_at


def test_watermark_is_clamped_to_max_lookback(fake_redis: fakeredis.FakeRedis) -> None:
    """Знак отстал на годы (воркер стоял) — не разворачиваем скан на всю историю."""
    from app.config import get_settings

    started_at = datetime.now(timezone.utc)
    fake_redis.set(WATERMARK_KEY, (started_at - timedelta(days=365)).isoformat())

    since = _load_watermark(started_at)

    lookback = timedelta(hours=get_settings().dashboard_warm_max_lookback_hours)
    assert since == started_at - lookback


def test_broken_watermark_does_not_break_the_warm(fake_redis: fakeredis.FakeRedis) -> None:
    started_at = datetime.now(timezone.utc)
    fake_redis.set(WATERMARK_KEY, "не-дата")

    assert _load_watermark(started_at) == started_at


def test_stale_cache_is_recomputed_on_read(db_session: Session) -> None:
    """Страховка на чтении: промах прогрева живёт максимум сутки, а не вечно."""
    from app.config import get_settings

    max_age = timedelta(hours=get_settings().dashboard_cache_max_age_hours)
    fresh = DashboardCache(
        user_id=uuid4(),
        computed_at=datetime.now(timezone.utc) - max_age + timedelta(minutes=5),
        stats={},
    )
    expired = DashboardCache(
        user_id=uuid4(),
        computed_at=datetime.now(timezone.utc) - max_age - timedelta(minutes=5),
        stats={},
    )

    assert _dashboard_cache_is_stale(fresh) is False
    assert _dashboard_cache_is_stale(expired) is True
