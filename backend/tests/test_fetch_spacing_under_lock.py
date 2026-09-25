"""Пауза между запросами к S95 и parkrun держится и при конкуренции (SYNC-OTHER-03).

До 21.09.2026 `wait_for_turn` стоял только ДО замка. Два процесса, отстоявшие
паузу одновременно (воркер и API-превью профиля), брали замок по очереди и
уходили на сайт с разницей в секунды: второй ждал только замок, а не интервал.
Теперь интервал проверяется ещё раз под замком — уже по свежей отметке первого.

Интервал в тестах 2,5 с: замок опрашивается раз в секунду, и при меньшем
интервале старый код проходил бы проверку случайно, за счёт самого опроса.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

SPACING_SECONDS = 2.5
FETCH_SECONDS = 0.3


class _Stub:
    """Фетчер-заглушка: запоминает момент каждого запроса и «качает» 0,3 с."""

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.started: list[float] = []
        self._lock = threading.Lock()

    def __call__(self, url: str, **_kwargs: object) -> str:
        with self._lock:
            self.started.append(time.monotonic())
        time.sleep(FETCH_SECONDS)
        return self.payload


def _run_two_concurrent(target) -> list[BaseException]:
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            target()
        except BaseException as exc:  # noqa: BLE001 — падение потока должно попасть в ассерт
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return errors


def _s95_settings() -> SimpleNamespace:
    return SimpleNamespace(
        s95_fetch_min_interval_seconds=SPACING_SECONDS,
        s95_fetch_max_interval_seconds=SPACING_SECONDS,
        s95_fetch_lock_timeout_seconds=60,
        s95_fetch_lock_blocking_seconds=30,
    )


def _parkrun_settings() -> SimpleNamespace:
    return SimpleNamespace(
        parkrun_fetch_min_interval_seconds=SPACING_SECONDS,
        parkrun_fetch_max_interval_seconds=SPACING_SECONDS,
        parkrun_fetch_lock_timeout_seconds=60,
        parkrun_fetch_lock_blocking_seconds=30,
        parkrun_use_cdp_for_fetch=False,
        parkrun_cdp_url="",
        parkrun_server_fetch_enabled=True,
    )


@pytest.mark.timeout(40)
def test_s95_concurrent_fetches_keep_spacing() -> None:
    from app.s95.fetch.coordinator import fetch_page_html

    stub = _Stub("<html>ok</html>")
    with (
        patch("app.s95.fetch.rate_limit.get_settings", return_value=_s95_settings()),
        patch("app.s95.fetch.lock.get_settings", return_value=_s95_settings()),
        patch("app.s95.fetch.coordinator.fetch_html_with_httpx", stub),
    ):
        errors = _run_two_concurrent(lambda: fetch_page_html("https://s95.ru/athletes/1/", reason="test"))

    assert not errors, errors
    assert len(stub.started) == 2
    first, second = sorted(stub.started)
    # Небольшой допуск на таймеры: важно, что второй не ушёл через секунду.
    assert second - first >= SPACING_SECONDS - 0.1


@pytest.mark.timeout(40)
def test_parkrun_concurrent_fetches_keep_spacing() -> None:
    from app.parkrun.fetch.coordinator import fetch_page_html

    # Длиннее 500 байт и с меткой parkrun — иначе диагностика сочтёт страницу защитой.
    stub = _Stub("<html><body>parkrunner " + "x" * 600 + "</body></html>")
    with (
        patch("app.parkrun.fetch.rate_limit.get_settings", return_value=_parkrun_settings()),
        patch("app.parkrun.fetch.lock.get_settings", return_value=_parkrun_settings()),
        patch("app.parkrun.fetch.coordinator.get_settings", return_value=_parkrun_settings()),
        patch("app.parkrun.fetch.coordinator.fetch_html_with_browser", stub),
        patch("app.parkrun.fetch.coordinator.save_browser_session", return_value=False),
    ):
        errors = _run_two_concurrent(lambda: fetch_page_html("https://www.parkrun.org.uk/parkrunner/1/", reason="test"))

    assert not errors, errors
    assert len(stub.started) == 2
    first, second = sorted(stub.started)
    assert second - first >= SPACING_SECONDS - 0.1
