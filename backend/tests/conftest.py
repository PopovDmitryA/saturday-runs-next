"""Shared pytest fixtures.

Database tests run inside a single outer transaction that is rolled back after
each test. ``session.commit()`` only releases a SAVEPOINT, so test helpers may
keep calling commit() without persisting rows to the real database.
"""

from __future__ import annotations

from collections.abc import Generator

import fakeredis
import pytest
from sqlalchemy.orm import Session

from app.db.session import get_engine


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def _isolated_redis(monkeypatch: pytest.MonkeyPatch, fake_redis: fakeredis.FakeRedis) -> None:
    """Keep rate-limit / abuse counters out of the dev and CI Redis instances."""
    fake_redis.flushdb()
    monkeypatch.setattr("app.core.redis_client._test_redis_override", fake_redis)


@pytest.fixture(scope="function")
def db_session() -> Generator[Session, None, None]:
    try:
        engine = get_engine()
        connection = engine.connect()
    except Exception:
        pytest.skip("Database not available")

    transaction = connection.begin()
    # join_transaction_mode="create_savepoint" — штатный механизм SQLAlchemy 2.0:
    # сессия работает внутри SAVEPOINT, поэтому commit() в тестируемом коде
    # (например, в sync_location) освобождает savepoint, но не пишет в реальную БД,
    # и внешний rollback в finally по-прежнему откатывает всё.
    # Прежний хак на after_transaction_end терял строки, созданные до commit()
    # (SyncRun пропадал → ObjectDeletedError).
    session = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
