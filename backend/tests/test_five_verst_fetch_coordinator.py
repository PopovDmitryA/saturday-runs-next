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
