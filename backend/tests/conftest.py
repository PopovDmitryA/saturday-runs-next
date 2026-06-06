"""Shared pytest fixtures.

Database tests run inside a single outer transaction that is rolled back after
each test. ``session.commit()`` only releases a SAVEPOINT, so test helpers may
keep calling commit() without persisting rows to the real database.
"""

from __future__ import annotations

from collections.abc import Generator

import fakeredis
import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db.session import get_engine


@pytest.fixture(autouse=True)
def _isolated_abuse_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep rate-limit / abuse counters out of the dev Redis instance."""
    redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.abuse_store.get_redis", lambda: redis)
    monkeypatch.setattr("app.core.abuse_protection.get_redis", lambda: redis)


@pytest.fixture(scope="function")
def db_session() -> Generator[Session, None, None]:
    try:
        engine = get_engine()
        connection = engine.connect()
    except Exception:
        pytest.skip("Database not available")

    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(session: Session, transaction_obj) -> None:  # noqa: ARG001
        if transaction_obj.nested and not transaction_obj._parent.nested:
            session.begin_nested()

    try:
        yield session
    finally:
        event.remove(session, "after_transaction_end", _restart_savepoint)
        session.close()
        transaction.rollback()
        connection.close()
