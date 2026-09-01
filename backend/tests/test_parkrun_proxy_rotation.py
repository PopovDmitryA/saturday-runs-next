"""Перебор выходов при защите WAF в httpx-режиме демона очереди.

Раньше первый же отказ WAF останавливал всю пачку, и очередь ждала человека.
С пулом прокси сессия обязана попробовать остальные выходы, прежде чем сдаться.
"""

from __future__ import annotations

import pytest

from app.parkrun.errors import ParkrunBanDetected
from app.parkrun.fetch import daemon_session as ds

THREE = ["socks5://127.0.0.1:1", "socks5://127.0.0.1:2", "socks5://127.0.0.1:3"]

# То, что inspect_html_response считает защитой: слишком короткий ответ.
BLOCKED = "<html>nope</html>"
GOOD = "<html>" + ("x" * 40000) + "</html>"


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text


class _Client:
    """Отдаёт заранее заданный ответ; считает обращения."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def get(self, url, cookies=None):  # noqa: ANN001, ARG002
        self.calls += 1
        return _Response(self.text)

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _no_redis_no_sleep(monkeypatch):
    """Отключаем всё, что ходит в Redis и спит: проверяем только логику перебора."""
    for name in (
        "wait_for_turn",
        "mark_fetch_completed",
        "clear_captcha_pending",
        "set_captcha_pending",
        "escalate_ban_cooldown",
        "clear_platform_cooldown",
    ):
        monkeypatch.setattr(ds, name, lambda *a, **k: None, raising=True)


def _session(pages: list[str], proxies: list[str]) -> ds.ParkrunDaemonSession:
    """Сессия, которая на каждом следующем выходе отдаёт следующую страницу."""
    s = ds.ParkrunDaemonSession(use_httpx=True, proxies=proxies)
    s.solve_captcha = False
    s._solver = None
    s.show_status = lambda *a, **k: None  # type: ignore[method-assign]
    seq = iter(pages)

    def fake_rebuild() -> None:
        s._httpx_client = _Client(next(seq))

    s._rebuild_transport = fake_rebuild  # type: ignore[method-assign]
    fake_rebuild()
    return s


def test_tries_every_proxy_before_giving_up() -> None:
    s = _session([BLOCKED, BLOCKED, BLOCKED], THREE)
    with pytest.raises(ParkrunBanDetected) as err:
        s._fetch_httpx("https://www.parkrun.org.uk/parkrunner/620/")
    assert s._proxies.rotations == 2, "должен был сменить выход дважды"
    assert "Перебрано выходов: 3" in str(err.value)


def test_stops_rotating_once_a_proxy_works() -> None:
    s = _session([BLOCKED, GOOD], THREE)
    html = s._fetch_httpx("https://www.parkrun.org.uk/parkrunner/620/")
    assert html == GOOD
    assert s._proxies.rotations == 1, "второй выход подошёл — дальше крутить незачем"


def test_without_proxies_behaves_as_before() -> None:
    """Одна попытка и остановка пачки — прежнее поведение на проде."""
    s = _session([BLOCKED], [])
    with pytest.raises(ParkrunBanDetected):
        s._fetch_httpx("https://www.parkrun.org.uk/parkrunner/620/")
    assert s._proxies.rotations == 0
