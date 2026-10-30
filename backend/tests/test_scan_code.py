"""Штрихкод участника для кнопки «Мой штрихкод»: тот же код, что на сайте,
и кнопки сообщения в каждом канале."""

from __future__ import annotations

import json

import pytest

from app.services import notification_senders as senders
from app.services.notification_senders import MessageButton, OutgoingMessage
from app.services.scan_code_service import qr_png, scan_code
from app.services.vk_client import VkApiError


@pytest.mark.parametrize(
    ("platform", "barcode", "external", "expected"),
    [
        ("five_verst", None, "790123", "A790123"),
        ("five_verst", "a790123", "1", "A790123"),
        ("s95", "А 512 340", None, "A512340"),
        ("s95", None, "123", None),
        ("s95", "XYZ", None, None),
    ],
)
def test_scan_code_matches_the_site(platform: str, barcode: str | None, external: str | None, expected: str | None) -> None:
    assert scan_code(platform, barcode, external) == expected


def test_qr_is_a_png() -> None:
    assert qr_png("A790123").startswith(b"\x89PNG")


def _message(*buttons: MessageButton) -> OutgoingMessage:
    return OutgoingMessage(
        title="Завтра старт",
        text="Текст",
        url=None,
        url_label="Мои планы",
        unsubscribe_url="https://run5k.test/u",
        settings_url="https://run5k.test/settings#notifications",
        buttons=buttons,
    )


def test_buttons_per_channel() -> None:
    route = MessageButton(label="🧭 Проложить маршрут", url="https://yandex.ru/maps/?rtext=~1,2&rtt=auto")
    code = MessageButton(label="🏷 Мой штрихкод", url="https://run5k.test/dashboard#scancode=s95", callback="scancode:s95")
    bot_only = MessageButton(label="Только бот", callback="x")
    message = _message(route, code, bot_only)
    assert message.telegram_markup() == {
        "inline_keyboard": [
            [{"text": "🧭 Проложить маршрут", "url": "https://yandex.ru/maps/?rtext=~1,2&rtt=auto"}],
            [{"text": "🏷 Мой штрихкод", "callback_data": "scancode:s95"}],
            [{"text": "Только бот", "callback_data": "x"}],
        ]
    }
    keyboard = json.loads(message.vk_keyboard() or "{}")
    assert keyboard["inline"] is True
    assert [row[0]["action"]["label"] for row in keyboard["buttons"]] == ["🧭 Проложить маршрут", "🏷 Мой штрихкод"]
    assert _message().telegram_markup() is None and _message().vk_keyboard() is None


def test_vk_without_bot_features_gets_links_in_text(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_send(token: str, peer_id: int, text: str, **kwargs: object) -> int:
        calls.append({"text": text, **kwargs})
        if kwargs.get("keyboard"):
            raise VkApiError("{'error_code': 912, 'error_msg': 'This is a chat bot feature'}")
        return 1

    monkeypatch.setattr(senders, "send_vk_message", fake_send)
    route = MessageButton(label="🧭 Проложить маршрут", url="https://yandex.ru/maps/?rtext=~1,2&rtt=auto")
    senders._send_vk_with_keyboard("token", 42, _message(route))
    assert len(calls) == 2
    assert "keyboard" not in calls[1]
    assert "🧭 Проложить маршрут: https://yandex.ru/maps/?rtext=~1,2&rtt=auto" in str(calls[1]["text"])


def test_vk_privacy_errors_are_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_send(token: str, peer_id: int, text: str, **kwargs: object) -> int:
        raise VkApiError("{'error_code': 901}")

    monkeypatch.setattr(senders, "send_vk_message", fake_send)
    with pytest.raises(VkApiError):
        senders._send_vk_with_keyboard("token", 42, _message(MessageButton(label="a", url="https://x.test")))


def test_bot_prefix_matches_the_reminder_button() -> None:
    from app.services.plan_reminder_extras import SCAN_CODE_CALLBACK_PREFIX
    from bot_app.scan_code import SCAN_CODE_PREFIX

    assert SCAN_CODE_PREFIX == SCAN_CODE_CALLBACK_PREFIX
