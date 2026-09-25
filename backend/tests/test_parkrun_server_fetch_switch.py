"""PARKRUN_SERVER_FETCH_ENABLED: сервер сайта сам в parkrun не ходит.

На домашнем сервере адрес общий со всем домом, и parkrun оттуда разбирает
только очередь через VPN-выходы. Сайт там же не должен идти в parkrun со
своего адреса ни по «Обновить», ни серверным разбором очереди.
"""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.parkrun.errors import ParkrunBanDetected
from app.services.sync_error_format import humanize_sync_error_message


def _settings(enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(parkrun_server_fetch_enabled=enabled, parkrun_use_cdp_for_fetch=False, parkrun_cdp_url="")


def test_disabled_server_fetch_never_opens_browser() -> None:
    from app.parkrun.fetch import coordinator

    with (
        patch("app.parkrun.fetch.coordinator.get_settings", return_value=_settings(False)),
        patch("app.parkrun.fetch.daemon_session.get_active_daemon_session", return_value=None),
        patch("app.parkrun.fetch.coordinator.fetch_html_with_browser") as browser,
    ):
        with pytest.raises(ParkrunBanDetected, match="server fetch disabled"):
            coordinator.fetch_page_html("https://www.parkrun.org.uk/parkrunner/1/", reason="profile-summary")
    browser.assert_not_called()


def test_daemon_path_is_not_affected() -> None:
    """Демон очереди ходит через VPN-выходы — выключатель его не касается."""
    from app.parkrun.fetch import coordinator

    daemon = SimpleNamespace(fetch_page_html=lambda url, **_: "<html>ok</html>")
    with (
        patch("app.parkrun.fetch.coordinator.get_settings", return_value=_settings(False)),
        patch("app.parkrun.fetch.daemon_session.get_active_daemon_session", return_value=daemon),
        patch("app.parkrun.fetch.coordinator.parkrun_fetch_lock", return_value=nullcontext()),
    ):
        assert coordinator.fetch_page_html("https://www.parkrun.org.uk/parkrunner/1/") == "<html>ok</html>"


def test_enabled_by_default() -> None:
    from app.config import Settings
    from app.parkrun.fetch import coordinator

    assert Settings.model_fields["parkrun_server_fetch_enabled"].default is True
    with patch("app.parkrun.fetch.coordinator.get_settings", return_value=_settings(True)):
        coordinator._check_server_fetch_allowed()  # не бросает


def test_disabled_message_is_not_a_ban_for_the_queue() -> None:
    """Строка очереди не должна получить срок охлаждения: демон берёт её сразу."""
    from app.parkrun.fetch.coordinator import SERVER_FETCH_DISABLED_MESSAGE
    from app.platform_fetch.cooldown import parse_cooldown_until_from_message

    assert parse_cooldown_until_from_message(SERVER_FETCH_DISABLED_MESSAGE) is None


def test_user_sees_queue_message_not_a_ban() -> None:
    from app.parkrun.fetch.coordinator import SERVER_FETCH_DISABLED_MESSAGE

    text = humanize_sync_error_message(f"parkrun: {SERVER_FETCH_DISABLED_MESSAGE}", "parkrun")
    assert text is not None
    assert "очеред" in text
    assert "недоступ" not in text
