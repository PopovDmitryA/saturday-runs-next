from __future__ import annotations

from typing import Any

import pytest

from app.services import vk_client


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self._payload


def _capture_post(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_post(url: str, *, data: dict[str, Any], timeout: float) -> _FakeResponse:
        captured["url"] = url
        captured["data"] = data
        captured["timeout"] = timeout
        return _FakeResponse(payload)

    monkeypatch.setattr(vk_client.httpx, "post", fake_post)
    return captured


def test_send_vk_message_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_post(monkeypatch, {"response": 123})
    result = vk_client.send_vk_message("tok", 42, "hi", reply_to=7)

    assert result == 123
    assert captured["url"].endswith("/messages.send")
    data = captured["data"]
    assert data["access_token"] == "tok"
    assert data["v"] == vk_client.VK_API_VERSION
    assert data["peer_id"] == 42
    assert data["message"] == "hi"
    assert data["reply_to"] == 7
    assert "random_id" in data


def test_send_vk_message_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_post(monkeypatch, {"response": 1})
    vk_client.send_vk_message("tok", 42, "x" * 5000)
    assert len(captured["data"]["message"]) == vk_client.VK_MESSAGE_LIMIT


def test_call_vk_api_raises_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_post(monkeypatch, {"error": {"error_code": 5, "error_msg": "auth"}})
    with pytest.raises(vk_client.VkApiError):
        vk_client.call_vk_api("messages.send", "tok", peer_id=1)
