from unittest.mock import patch

import pytest

from app.s95.fetch.priority import (
    S95YieldForUserSync,
    check_yield_for_user_sync,
    s95_user_sync_context,
)


def test_check_yield_for_user_sync_raises_when_user_queue_pending() -> None:
    with patch("app.s95.fetch.priority.s95_user_queue_has_pending", return_value=True):
        with pytest.raises(S95YieldForUserSync):
            check_yield_for_user_sync()


def test_check_yield_for_user_sync_skips_during_user_sync() -> None:
    with patch("app.s95.fetch.priority.s95_user_queue_has_pending", return_value=True):
        with s95_user_sync_context():
            check_yield_for_user_sync()

from app.s95.ban import is_ban_or_protection_html
from app.s95.errors import S95BanDetected
from app.s95.messages import s95_user_facing_error


def test_is_ban_or_protection_html_detects_chromium_forbidden_page() -> None:
    html = (
        '<html><head><meta name="color-scheme" content="light dark"></head>'
        '<body><pre style="word-wrap: break-word; white-space: pre-wrap;">Forbidden\n</pre></body></html>'
    )
    assert is_ban_or_protection_html(html) is True


def test_s95_user_facing_error_for_http_403() -> None:
    message = s95_user_facing_error(S95BanDetected("HTTP 403 Forbidden from S95 for https://s95.ru/athletes/1/"))
    assert "403" in message
    assert "заблокировал доступ" in message


def test_s95_user_facing_error_for_cooldown() -> None:
    message = s95_user_facing_error(S95BanDetected("S95 fetch in cooldown until 1710000000 (now 1709990000)"))
    assert "приостановлены" in message
