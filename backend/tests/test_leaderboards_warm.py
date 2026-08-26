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
    # Карта туристов прогревается тем же проходом, но ходит в базу своим
    # запросом — на фейковой сессии её подменяем.
    monkeypatch.setattr(
        leaderboards_warm,
        "refresh_tourist_map_cache",
        lambda *_args, **_kwargs: events.append("tourist-map") or 0,
    )
    # Тем же проходом греются рекорды локаций — отдельный снапшот со своими
    # запросами к базе; на фейковой сессии подменяем и его.
    monkeypatch.setattr(
        leaderboards_warm,
        "refresh_location_records_rating_cache",
        lambda *_args, **_kwargs: events.append("location-records") or 0,
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


def test_sync_schedules_warm_once_per_debounce_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Серия синков подряд ставит один пересчёт, а не очередь из полных прогонов.

    В субботу протоколы приезжают ежечасно и сразу тремя системами (5 вёрст,
    S95, RunPark) — без склейки каждый синк уводил бы воркер на полную сетку.
    """
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        leaderboards_warm.warm_leaderboards_cache,
        "apply_async",
        lambda **kwargs: sent.append(kwargs),
    )

    assert leaderboards_warm.schedule_leaderboards_warm() is True
    assert leaderboards_warm.schedule_leaderboards_warm() is False

    assert len(sent) == 1
    assert sent[0]["queue"] == "runpark"
    # Пауза перед стартом — чтобы протоколы соседних систем попали в тот же прогон.
    assert sent[0]["countdown"] > 0


def test_warm_skips_when_another_run_holds_lock(
    journal: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Прогон от синка и прогон по расписанию не считают одну сетку вдвоём."""
    monkeypatch.setattr(
        leaderboards_warm,
        "refresh_leaderboard_cache",
        lambda *_args, **_kwargs: {"entrants": 1},
    )
    assert leaderboards_warm._acquire_running_lock() is True

    results = leaderboards_warm.warm_leaderboards_cache()

    assert results == {"skipped": "already_running"}
    assert journal == [], "занятый замок не должен пускать прогон к базе"


def test_warm_releases_lock_for_the_next_run(
    journal: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Замок снимается по окончании — иначе следующий прогрев молчал бы до TTL."""
    monkeypatch.setattr(
        leaderboards_warm,
        "refresh_leaderboard_cache",
        lambda *_args, **_kwargs: {"entrants": 1},
    )

    leaderboards_warm.warm_leaderboards_cache()

    assert leaderboards_warm._acquire_running_lock() is True


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
    monkeypatch.setattr(
        leaderboards_warm, "refresh_tourist_map_cache", lambda *_args, **_kwargs: 3
    )

    results = leaderboards_warm.warm_leaderboards_cache()

    assert results
    assert all(value == 3 for value in results.values())


def test_warm_refreshes_tourist_map_for_base_variant(
    journal: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Карта туристов прогревается вместе с рейтингом — по разу на метрику.

    Матрица карты привязана к built_at снапшота, поэтому каждый прогрев её
    обесценивает: без этого шага первый, кто раскроет спойлер, ждал бы расчёт
    прямо в запросе. Прогреваем только базовый вариант фильтров — на остальные
    сочетания заходят единицы.
    """
    monkeypatch.setattr(
        leaderboards_warm,
        "refresh_leaderboard_cache",
        lambda *_args, **_kwargs: {"entrants": 5},
    )

    results = leaderboards_warm.warm_leaderboards_cache()

    warmed = [key for key in results if key.endswith(":tmap")]
    assert warmed == ["locations:tmap", "volunteer_locations:tmap"]
    assert journal.count("tourist-map") == 2


def test_broken_tourist_map_does_not_fail_the_rating(
    journal: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Упавшая карта не портит прогрев таблицы: она — дополнение к рейтингу."""
    monkeypatch.setattr(
        leaderboards_warm,
        "refresh_leaderboard_cache",
        lambda *_args, **_kwargs: {"entrants": 5},
    )

    def boom(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("нет соединения")

    monkeypatch.setattr(leaderboards_warm, "refresh_tourist_map_cache", boom)

    results = leaderboards_warm.warm_leaderboards_cache()

    assert "error" not in results.values()
    assert not [key for key in results if key.endswith(":tmap")]
