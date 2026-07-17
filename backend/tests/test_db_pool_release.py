from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import (
    get_engine,
    get_session_factory,
    release_db_connection_for_network_io,
)


def test_release_returns_connection_to_pool_and_session_stays_usable() -> None:
    """Коннект возвращается в пул на время сетевого фетча, сессия живёт дальше."""
    try:
        engine = get_engine()
        db = get_session_factory()()
        db.execute(text("select 1"))
    except Exception:
        pytest.skip("Database not available")

    try:
        held = engine.pool.checkedout()
        assert held >= 1

        release_db_connection_for_network_io(db)
        assert engine.pool.checkedout() == held - 1

        # Сессия остаётся рабочей: следующий запрос берёт новый коннект.
        assert db.execute(text("select 1")).scalar() == 1
    finally:
        db.close()


def test_fetch_live_preview_bundle_releases_before_network_io(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Порядок строгий: сначала вернуть коннект в пул, потом идти в сеть."""
    from app.services.profile_preview_persist import fetch_live_preview_bundle

    order: list[str] = []
    monkeypatch.setattr(
        "app.db.session.release_db_connection_for_network_io",
        lambda db: order.append("release"),
    )

    def fake_fetch(profile_url: str):
        order.append("fetch")
        raise RuntimeError("network stub")

    monkeypatch.setattr("app.platform_adapters.s95.parser.fetch_profile_activity", fake_fetch)

    with pytest.raises(RuntimeError, match="network stub"):
        fetch_live_preview_bundle(db_session, "s95", "https://s95.ru/athletes/1")

    assert order == ["release", "fetch"]
