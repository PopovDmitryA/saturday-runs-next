"""Адреса запросов к Telegram (в них токен бота) не попадают в журнал."""

from __future__ import annotations

import logging

import httpx
import pytest

from app.core.log_hygiene import quiet_http_client_logs

TOKEN_URL = "https://api.telegram.org/bot123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA/sendMessage"


def test_httpx_request_line_with_bot_token_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    quiet_http_client_logs()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    with caplog.at_level(logging.INFO), httpx.Client(transport=transport) as client:
        client.post(TOKEN_URL, json={"chat_id": 1, "text": "x"})
    assert "AAAAAAAA" not in caplog.text


def test_entry_points_quiet_http_client_logs() -> None:
    import app.main  # noqa: F401
    import app.workers.celery_app  # noqa: F401

    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING
