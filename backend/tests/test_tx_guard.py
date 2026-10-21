"""Сторож простоя транзакции (app/db/tx_guard.py).

Время подменяется в модуле сторожа, база — SQLite в памяти: сторож работает
на событиях движка SQLAlchemy и от Postgres не зависит.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app.db import tx_guard


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(tx_guard, "time", SimpleNamespace(monotonic=fake.monotonic))
    return fake


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    eng = create_engine("sqlite://")
    assert tx_guard.install_tx_guard(eng, idle_seconds=30) is not None
    yield eng
    eng.dispose()


def _select(conn: Connection | Session) -> None:
    conn.execute(text("select 1"))


def test_sql_after_long_idle_inside_transaction_fails_under_pytest(engine: Engine, clock: _Clock) -> None:
    with engine.connect() as conn:
        _select(conn)
        clock.advance(31)
        with pytest.raises(AssertionError, match="SQL после 31 с простоя внутри транзакции — Postgres рвёт на 60 с"):
            _select(conn)
        # Пауза «засчитана»: следующий запрос сразу за ней не падает повторно.
        _select(conn)


def test_commit_after_long_idle_fails(engine: Engine, clock: _Clock) -> None:
    with engine.connect() as conn:
        _select(conn)
        clock.advance(45)
        with pytest.raises(AssertionError, match="COMMIT после 45 с"):
            conn.commit()


def test_idle_from_begin_to_first_sql_counts(engine: Engine, clock: _Clock) -> None:
    with engine.connect() as conn:
        conn.begin()
        clock.advance(40)
        with pytest.raises(AssertionError, match="с начала транзакции без единого запроса"):
            _select(conn)


def test_commit_of_empty_transaction_is_fine(engine: Engine, clock: _Clock) -> None:
    with engine.connect() as conn:
        conn.begin()
        clock.advance(600)
        conn.commit()


def test_short_idle_is_fine(engine: Engine, clock: _Clock) -> None:
    with engine.connect() as conn:
        _select(conn)
        clock.advance(29)
        _select(conn)
        clock.advance(29)
        conn.commit()


def test_idle_outside_transaction_is_fine(engine: Engine, clock: _Clock) -> None:
    with Session(engine) as db:
        _select(db)
        db.commit()
        clock.advance(600)  # «сеть» после commit — так и надо
        _select(db)
        db.commit()


def test_session_idle_inside_transaction_fails(engine: Engine, clock: _Clock) -> None:
    with Session(engine) as db:
        _select(db)
        clock.advance(61)  # «сеть» без release_before_fetch
        with pytest.raises(AssertionError, match="SQL после 61 с"):
            _select(db)


def test_autocommit_connection_is_fine(engine: Engine, clock: _Clock) -> None:
    # Соединение advisory-lock в AUTOCOMMIT: транзакции на сервере нет.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        _select(conn)
        clock.advance(600)
        _select(conn)


def test_prod_warns_with_stack_once_per_hour_per_place(
    engine: Engine, clock: _Clock, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(tx_guard, "is_test_run", lambda: False)
    caplog.set_level(logging.WARNING, logger=tx_guard.logger.name)

    with engine.connect() as conn:
        _select(conn)
        for pause in (31, 31, 3600):
            clock.advance(pause)
            _select(conn)  # на проде запрос не ломается

    warnings = [r.getMessage() for r in caplog.records if r.name == tx_guard.logger.name]
    assert len(warnings) == 2, warnings  # второй раз — в пределах часа, молчим
    assert warnings[0].startswith("SQL после 31 с простоя внутри транзакции — Postgres рвёт на 60 с")
    assert "Запрос: select 1" in warnings[0]
    assert "tests/test_tx_guard.py" in warnings[0] and "in _select" in warnings[0]
    assert warnings[1].startswith("SQL после 3600 с")


def test_threshold_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db.session import tx_guard_idle_seconds

    monkeypatch.delenv("DB_TX_GUARD_DISABLED", raising=False)
    monkeypatch.delenv("DB_DISABLE_IDLE_TX_TIMEOUT", raising=False)
    monkeypatch.setenv("DB_TX_GUARD_IDLE_SECONDS", "5")
    assert tx_guard_idle_seconds() == 5
    monkeypatch.setenv("DB_TX_GUARD_IDLE_SECONDS", "мусор")
    assert tx_guard_idle_seconds() == tx_guard.DEFAULT_IDLE_SECONDS


def test_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db.session import tx_guard_idle_seconds

    monkeypatch.setenv("DB_TX_GUARD_DISABLED", "1")
    assert tx_guard_idle_seconds() is None
    monkeypatch.delenv("DB_TX_GUARD_DISABLED")
    monkeypatch.setenv("DB_DISABLE_IDLE_TX_TIMEOUT", "1")
    assert tx_guard_idle_seconds() is None


def test_app_engine_is_guarded() -> None:
    from app.db.session import tx_guard_idle_seconds

    if tx_guard_idle_seconds() is None:
        pytest.skip("сторож выключен окружением")
    from app.db.session import get_engine

    assert tx_guard.guard_for(get_engine()) is not None
