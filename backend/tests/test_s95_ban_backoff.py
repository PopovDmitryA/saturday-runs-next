"""Отказ s95 поднимает охлаждение по лестнице, а не на плоский час.

24.08.2026 s95 сутки отвечал проду 403. Охлаждение было одно и на час — короче
четырёхчасового интервала расписания, поэтому не подавляло ни одного прогона:
мы постучались девять раз подряд и 25.08 получили уже отказ на фаерволе.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import fakeredis
import httpx
import pytest

from app.s95.errors import S95BanDetected
from app.s95.fetch.ban_state import (
    ban_cooldown_level,
    clear_ban_cooldown,
    escalate_ban_cooldown,
)
from app.s95.fetch.coordinator import fetch_json
from app.s95.fetch.http import fetch_html_with_httpx, fetch_json_with_httpx


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def redis_patched(fake_redis: fakeredis.FakeRedis):
    with (
        patch("app.s95.fetch.ban_state.get_redis_client", return_value=fake_redis),
        patch("app.s95.fetch.rate_limit.get_redis_client", return_value=fake_redis),
        patch("app.s95.fetch.lock.get_redis_client", return_value=fake_redis),
    ):
        yield fake_redis


def test_cooldown_grows_with_each_refusal(redis_patched: fakeredis.FakeRedis) -> None:
    now = time.time()
    first = escalate_ban_cooldown()
    second = escalate_ban_cooldown()

    assert first - now == pytest.approx(3600, abs=5)
    assert second - now == pytest.approx(18000, abs=5)
    assert ban_cooldown_level() == 2


def test_successful_fetch_resets_the_ladder(redis_patched: fakeredis.FakeRedis) -> None:
    escalate_ban_cooldown()
    escalate_ban_cooldown()
    clear_ban_cooldown()

    assert ban_cooldown_level() == 0
    now = time.time()
    assert escalate_ban_cooldown() - now == pytest.approx(3600, abs=5)


def test_longest_step_repeats_instead_of_starting_over(redis_patched: fakeredis.FakeRedis) -> None:
    """Уровней больше, чем ступеней — держим последнюю, а не падаем на первую."""
    for _ in range(6):
        until = escalate_ban_cooldown()

    now = time.time()
    assert until - now == pytest.approx(604800, abs=5)


def test_connection_refused_counts_as_refusal() -> None:
    """25.08 s95 перешёл с 403 на TCP RST — это то же «уходи», а не сетевой сбой."""
    with (
        patch("httpx.get", side_effect=httpx.ConnectError("[Errno 111] Connection refused")),
        pytest.raises(S95BanDetected, match="отказал в соединении"),
    ):
        fetch_json_with_httpx("https://s95.ru/pages.json")


def test_timeout_stays_an_ordinary_error() -> None:
    """Таймаут — не бан: поднимать за него лестницу до недели не за что."""
    with (
        patch("httpx.get", side_effect=httpx.ReadTimeout("timed out")),
        pytest.raises(RuntimeError),
    ):
        fetch_html_with_httpx("https://s95.ru/events/kuzminki")


def test_json_requests_wait_out_the_cooldown(redis_patched: fakeredis.FakeRedis) -> None:
    """JSON API ходит той же дверью, что и страницы: охлаждение действует и на него."""
    escalate_ban_cooldown()

    with patch("app.s95.fetch.coordinator.fetch_json_with_httpx") as fetcher:
        with pytest.raises(S95BanDetected, match="cooldown"):
            fetch_json("https://s95.ru/pages.json", reason="test")

    fetcher.assert_not_called()


def test_json_refusal_starts_the_cooldown(redis_patched: fakeredis.FakeRedis) -> None:
    with (
        patch(
            "app.s95.fetch.coordinator.fetch_json_with_httpx",
            side_effect=S95BanDetected("HTTP 403 from S95 for https://s95.ru/pages.json"),
        ),
        pytest.raises(S95BanDetected),
    ):
        fetch_json("https://s95.ru/pages.json", reason="test")

    assert ban_cooldown_level() == 1


def test_json_success_clears_the_cooldown(redis_patched: fakeredis.FakeRedis) -> None:
    escalate_ban_cooldown()
    clear_ban_cooldown()  # охлаждение снято руками, лестница должна уйти после успеха

    with patch("app.s95.fetch.coordinator.fetch_json_with_httpx", return_value={"events": []}):
        assert fetch_json("https://s95.ru/pages.json", reason="test") == {"events": []}

    assert ban_cooldown_level() == 0
