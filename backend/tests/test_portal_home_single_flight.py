"""Холодный пересчёт главной ведёт кто-то один (QRY-USER-ADMIN-03).

Пересчёт /api/portal/home занимает на проде ~2 минуты и идёт прямо в запросе.
Пока лока не было, каждый посетитель на пустом кэше запускал свой агрегат по
всей базе — на пуле 5+10 соединений на uvicorn-воркер это верный способ
положить сайт после бампа версии ключа.
"""

from __future__ import annotations

from unittest.mock import patch

import fakeredis
import pytest
from sqlalchemy.orm import Session

from app.services import portal_home_service
from app.services.portal_home_service import (
    PORTAL_HOME_CACHE_KEY,
    PORTAL_HOME_FALLBACK_KEY,
    PORTAL_HOME_LOCK_KEY,
    _await_portal_home_cache,
    _compute_portal_home,
    _write_portal_home_cache,
    build_portal_home,
)


def test_winner_fills_both_keys_and_releases_the_lock(
    db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    payload = _compute_portal_home(db_session)
    with patch(
        "app.services.portal_home_service._compute_portal_home", return_value=payload
    ) as compute:
        build_portal_home(db_session)

    assert compute.call_count == 1
    assert fake_redis.get(PORTAL_HOME_CACHE_KEY) is not None
    # Запасная копия — для тех, кто придёт на холодный кэш во время пересчёта.
    assert fake_redis.get(PORTAL_HOME_FALLBACK_KEY) is not None
    assert fake_redis.get(PORTAL_HOME_LOCK_KEY) is None


def test_lock_is_released_even_if_the_computation_fails(
    db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    with patch(
        "app.services.portal_home_service._compute_portal_home",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(RuntimeError):
            build_portal_home(db_session)
    assert fake_redis.get(PORTAL_HOME_LOCK_KEY) is None


def test_loser_serves_the_stale_snapshot_instead_of_recomputing(
    db_session: Session,
    fake_redis: fakeredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _compute_portal_home(db_session)
    _write_portal_home_cache(payload)
    # Версию ключа бампнули (или истёк TTL) — остался только запасной снимок.
    fake_redis.delete(PORTAL_HOME_CACHE_KEY)
    # …и пересчёт уже ведёт кто-то другой.
    fake_redis.set(PORTAL_HOME_LOCK_KEY, "1")
    monkeypatch.setattr(portal_home_service, "PORTAL_HOME_LOCK_WAIT_SECONDS", 0.0)

    with patch("app.services.portal_home_service._compute_portal_home") as compute:
        response = build_portal_home(db_session)

    compute.assert_not_called()
    assert response.generated_at is not None


def test_loser_computes_when_there_is_nothing_to_serve(
    db_session: Session,
    fake_redis: fakeredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ни свежего снимка, ни запасного — долгий ответ лучше пустой главной."""
    payload = _compute_portal_home(db_session)
    fake_redis.set(PORTAL_HOME_LOCK_KEY, "1")
    monkeypatch.setattr(portal_home_service, "PORTAL_HOME_LOCK_WAIT_SECONDS", 0.0)

    with patch(
        "app.services.portal_home_service._compute_portal_home", return_value=payload
    ) as compute:
        build_portal_home(db_session)

    assert compute.call_count == 1


def test_waiter_picks_up_the_snapshot_as_soon_as_it_appears(
    fake_redis: fakeredis.FakeRedis,
) -> None:
    _write_portal_home_cache({"hero": {"finishes_total": 1}})
    assert _await_portal_home_cache() == {"hero": {"finishes_total": 1}}
