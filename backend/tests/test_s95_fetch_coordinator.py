from __future__ import annotations

from unittest.mock import patch

import fakeredis
import pytest

from app.s95.errors import S95BanDetected
from app.s95.fetch.coordinator import fetch_page_html


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


def test_fetch_serializes_with_lock_and_rate_limit(fake_redis: fakeredis.FakeRedis) -> None:
    with (
        patch("app.s95.fetch.coordinator.get_redis_client", return_value=fake_redis),
        patch("app.s95.fetch.rate_limit.get_redis_client", return_value=fake_redis),
        patch("app.s95.fetch.lock.get_redis_client", return_value=fake_redis),
        patch("app.s95.fetch.coordinator.fetch_html_with_browser", return_value="<html>ok</html>"),
    ):
        html = fetch_page_html("https://s95.ru/athletes/1/", reason="test")
        assert "ok" in html


def test_fetch_detects_ban(fake_redis: fakeredis.FakeRedis) -> None:
    with (
        patch("app.s95.fetch.coordinator.get_redis_client", return_value=fake_redis),
        patch("app.s95.fetch.rate_limit.get_redis_client", return_value=fake_redis),
        patch("app.s95.fetch.lock.get_redis_client", return_value=fake_redis),
        patch(
            "app.s95.fetch.coordinator.fetch_html_with_browser",
            return_value="<html>recaptcha challenge</html>",
        ),
    ):
        with pytest.raises(S95BanDetected):
            fetch_page_html("https://s95.ru/athletes/1/", reason="test")
