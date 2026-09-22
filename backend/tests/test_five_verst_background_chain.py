"""Фоновая цепочка 5 вёрст не имеет права пережить субботу.

19.09.2026: пятничная цепочка сверки доедала протоколы 2023 года до полудня
субботы, к ней пристроились ещё две такие же (сверка стартует раз в три часа, а
цепочка при раздутой пачке идёт дольше), и приоритетная очередь простояла
нетронутой с самого переезда воркеров — 21 протухший `latest`, ноль субботних
протоколов. Инварианты ниже описывают три предохранителя: стена выходных,
срок годности звена и «одна цепочка на пайплайн».
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.services.scheduled_sync_guard import (
    chain_link_expires,
    is_freshness_window,
    try_start_background_chain,
)
from app.workers.tasks import five_verst_sync as fv

MOSCOW = ZoneInfo("Europe/Moscow")


@pytest.fixture
def chain_redis() -> MagicMock:
    """Живая метка цепочки в памяти: set(nx=True) второй раз отдаёт False."""

    redis = MagicMock()
    store: dict[str, str] = {}

    def _set(key: str, value: str, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and key in store:
            return None
        store[key] = value
        return True

    redis.set.side_effect = _set
    redis.delete.side_effect = lambda key: store.pop(key, None)
    with patch("app.services.scheduled_sync_guard.get_redis_client", return_value=redis):
        yield redis


def test_weekend_is_the_freshness_window() -> None:
    assert is_freshness_window(datetime(2026, 9, 19, 12, 0, tzinfo=MOSCOW)) is True
    assert is_freshness_window(datetime(2026, 9, 20, 12, 0, tzinfo=MOSCOW)) is True
    assert is_freshness_window(datetime(2026, 9, 21, 12, 0, tzinfo=MOSCOW)) is False


def test_link_expires_at_the_weekend_wall() -> None:
    """Звено, поставленное в пятницу вечером, до субботы не доживает."""

    friday_evening = datetime(2026, 9, 18, 23, 10, tzinfo=MOSCOW)
    assert chain_link_expires(friday_evening).astimezone(MOSCOW) == datetime(2026, 9, 19, 0, 0, tzinfo=MOSCOW)


def test_link_expires_in_two_hours_on_a_weekday() -> None:
    wednesday = datetime(2026, 9, 16, 10, 0, tzinfo=MOSCOW)
    assert chain_link_expires(wednesday).astimezone(MOSCOW) == datetime(2026, 9, 16, 12, 0, tzinfo=MOSCOW)


def test_second_chain_waits_for_the_first(chain_redis: MagicMock) -> None:
    assert try_start_background_chain("five_verst:reconcile") is True
    assert try_start_background_chain("five_verst:reconcile") is False
    # Ручной запуск (админка, /sync reconcile) забирает пайплайн себе.
    assert try_start_background_chain("five_verst:reconcile", force=True) is True


def _full_batch(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Прогон, который «набрал полную пачку» — значит, цепочке есть что продолжать."""

    enqueued: list[dict[str, object]] = []
    monkeypatch.setattr(fv, "run_reported_sync", lambda *a, **kw: {"candidates_total": 10, "errors": []})
    monkeypatch.setattr(
        fv.reconcile_stale_protocols_task,
        "apply_async",
        lambda **kwargs: enqueued.append(kwargs),
    )
    return enqueued


def test_chain_stops_at_the_weekend(monkeypatch: pytest.MonkeyPatch, chain_redis: MagicMock) -> None:
    enqueued = _full_batch(monkeypatch)
    monkeypatch.setattr(fv, "is_freshness_window", lambda *a, **kw: True)

    payload = fv.reconcile_stale_protocols_task.run(limit=10, chunks_left=5, force=True)

    assert enqueued == []
    assert payload["next_chunk_skipped"] == "freshness_window"


def test_chain_link_carries_an_expiry_on_a_weekday(
    monkeypatch: pytest.MonkeyPatch,
    chain_redis: MagicMock,
) -> None:
    enqueued = _full_batch(monkeypatch)
    monkeypatch.setattr(fv, "is_freshness_window", lambda *a, **kw: False)

    payload = fv.reconcile_stale_protocols_task.run(limit=10, chunks_left=5, force=True)

    assert payload["next_chunk_enqueued"] is True
    assert len(enqueued) == 1
    assert enqueued[0]["expires"] is not None
    assert enqueued[0]["kwargs"]["chunks_left"] == 4


def test_scheduled_run_skips_while_a_chain_is_alive(
    monkeypatch: pytest.MonkeyPatch,
    chain_redis: MagicMock,
) -> None:
    """Запуск по расписанию не начинает вторую цепочку поверх первой."""

    _full_batch(monkeypatch)
    monkeypatch.setattr(fv, "is_freshness_window", lambda *a, **kw: False)

    first = fv.reconcile_stale_protocols_task.run(limit=10, chunks_left=None)
    second = fv.reconcile_stale_protocols_task.run(limit=10, chunks_left=None)

    assert first.get("next_chunk_enqueued") is True
    assert second == {"skipped": True, "reason": "chain_already_running", "errors": []}
