from __future__ import annotations

from typing import Any

import pytest

from app.workers.tasks import leaderboards_warm


class _FakeSession:
    """Сессия-протокол: пишет в общий журнал, кто и когда её трогал."""

    def __init__(self, journal: list[str], *, close_raises: bool = False) -> None:
        self.journal = journal
        self.close_raises = close_raises

    def rollback(self) -> None:
        self.journal.append("rollback")

    def close(self) -> None:
        self.journal.append("close")
        if self.close_raises:
            raise RuntimeError("terminating connection due to idle-in-transaction timeout")


class _FakeSource:
    def __init__(self, journal: list[str]) -> None:
        self.journal = journal

    def release(self) -> None:
        self.journal.append("release")


@pytest.fixture
def journal(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []
    session = _FakeSession(events)
    monkeypatch.setattr(leaderboards_warm, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        leaderboards_warm, "make_snapshot_source", lambda _db: _FakeSource(events)
    )
    return events


def test_warm_closes_transaction_after_every_variant(
    journal: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Между вариантами транзакция закрывается — иначе её убивает Postgres.

    Сетка одного рейтинга считается в Python минутами, а соединение мы сами
    открываем с idle_in_transaction_session_timeout=60s. До 06.08.2026 задача
    держала одну транзакцию на весь прогон, Postgres рвал соединение, и первый
    же запрос следующего рейтинга падал (на проде — volunteer_locations, ровно
    после большой сетки locations).
    """

    def fake_refresh(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        journal.append("refresh")
        return {"entrants": 1}

    monkeypatch.setattr(leaderboards_warm, "refresh_leaderboard_cache", fake_refresh)

    leaderboards_warm.warm_leaderboards_cache()

    refreshes = [event for event in journal if event == "refresh"]
    assert refreshes, "сетка вариантов пуста — тест ничего не проверяет"
    # За каждым расчётом идёт закрытие транзакции, и ни один расчёт не стартует
    # с открытой с прошлого раза.
    for index, event in enumerate(journal):
        if event == "refresh":
            assert "rollback" in journal[index + 1 : index + 3]


def test_warm_survives_failing_variant(
    journal: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Упавший вариант не роняет прогон: помечаем 'error' и считаем дальше."""
    calls = {"count": 0}

    def fake_refresh(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("terminating connection due to idle-in-transaction timeout")
        return {"entrants": 7}

    monkeypatch.setattr(leaderboards_warm, "refresh_leaderboard_cache", fake_refresh)

    results = leaderboards_warm.warm_leaderboards_cache()

    assert list(results.values()).count("error") == 1
    assert 7 in results.values()
    assert journal[-1] == "close"


def test_warm_survives_broken_session_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ошибка на close() не отменяет уже разложенный по Redis прогрев.

    close() у SQLAlchemy делает ROLLBACK, и на убитом Postgres соединении сам
    бросает исключение: 05.08.2026 два прогона на проде так и завершились
    «Task raised unexpected», хотя кэш был посчитан и записан.
    """
    events: list[str] = []
    session = _FakeSession(events, close_raises=True)
    monkeypatch.setattr(leaderboards_warm, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        leaderboards_warm, "make_snapshot_source", lambda _db: _FakeSource(events)
    )
    monkeypatch.setattr(
        leaderboards_warm,
        "refresh_leaderboard_cache",
        lambda *_args, **_kwargs: {"entrants": 3},
    )

    results = leaderboards_warm.warm_leaderboards_cache()

    assert results
    assert all(value == 3 for value in results.values())
