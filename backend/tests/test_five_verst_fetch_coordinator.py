from __future__ import annotations

from unittest.mock import patch

import fakeredis
import pytest

from app.five_verst.errors import FiveVerstBanDetected
from app.five_verst.fetch.coordinator import fetch_page_html


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


def test_fetch_serializes_with_lock_and_rate_limit(fake_redis: fakeredis.FakeRedis) -> None:
    with (
        patch("app.five_verst.fetch.coordinator.get_redis_client", return_value=fake_redis),
        patch("app.five_verst.fetch.rate_limit.get_redis_client", return_value=fake_redis),
        patch("app.five_verst.fetch.lock.get_redis_client", return_value=fake_redis),
        patch("app.five_verst.fetch.coordinator._fetch_html_raw", return_value="<html>ok</html>"),
    ):
        html = fetch_page_html("https://5verst.ru/babushkinskynayauze/", reason="test")
        assert "ok" in html


def test_fetch_detects_ban(fake_redis: fakeredis.FakeRedis) -> None:
    with (
        patch("app.five_verst.fetch.coordinator.get_redis_client", return_value=fake_redis),
        patch("app.five_verst.fetch.rate_limit.get_redis_client", return_value=fake_redis),
        patch("app.five_verst.fetch.lock.get_redis_client", return_value=fake_redis),
        patch(
            "app.five_verst.fetch.coordinator._fetch_html_raw",
            return_value="<html>recaptcha challenge</html>",
        ),
    ):
        with pytest.raises(FiveVerstBanDetected):
            fetch_page_html("https://5verst.ru/babushkinskynayauze/", reason="test")


def test_batch_fetch_pauses_for_user_sync(fake_redis: fakeredis.FakeRedis) -> None:
    """Батч не лезет к сайту, пока идёт пользовательский синк, — но и не падает."""
    with (
        patch("app.five_verst.fetch.coordinator.get_redis_client", return_value=fake_redis),
        patch("app.five_verst.fetch.rate_limit.get_redis_client", return_value=fake_redis),
        patch("app.five_verst.fetch.lock.get_redis_client", return_value=fake_redis),
        patch("app.five_verst.fetch.coordinator._fetch_html_raw", return_value="<html>ok</html>"),
        patch("app.five_verst.fetch.coordinator.wait_for_user_sync_window") as pause,
    ):
        fetch_page_html("https://5verst.ru/babushkinskynayauze/", reason="reconcile")

    # Пауза проверяется до фетча и ещё раз после ожидания слота rate limit:
    # пользователь мог прийти, пока батч отстаивал свой интервал.
    assert pause.call_count == 2


def test_user_fetch_refreshes_its_own_mark(fake_redis: fakeredis.FakeRedis) -> None:
    """Пользовательский фетч продлевает отметку и никого не ждёт."""
    from app.five_verst.fetch.priority import five_verst_user_sync_context

    with (
        patch("app.five_verst.fetch.coordinator.get_redis_client", return_value=fake_redis),
        patch("app.five_verst.fetch.rate_limit.get_redis_client", return_value=fake_redis),
        patch("app.five_verst.fetch.lock.get_redis_client", return_value=fake_redis),
        patch("app.five_verst.fetch.priority.get_redis_client", return_value=fake_redis),
        patch("app.five_verst.fetch.coordinator._fetch_html_raw", return_value="<html>ok</html>"),
        patch("app.five_verst.fetch.coordinator.wait_for_user_sync_window") as pause,
        patch("app.five_verst.fetch.coordinator.mark_user_sync_alive") as keep_alive,
    ):
        with five_verst_user_sync_context(ttl_seconds=60):
            fetch_page_html("https://5verst.ru/babushkinskynayauze/", reason="user")

    pause.assert_not_called()
    keep_alive.assert_called_once()
