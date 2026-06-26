from __future__ import annotations

from typing import Any

import pytest

from vk_bot import vk_api


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self._payload


def _primed_poll(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]):
    """VkLongPoll with server/key/ts already set and httpx.get stubbed."""
    lp = vk_api.VkLongPoll()
    lp._server, lp._key, lp._ts = "srv", "key", "10"

    server_calls: list[bool] = []

    def fake_call(method: str, token: str, **params: Any) -> dict[str, Any]:
        server_calls.append(True)
        return {"server": "srv2", "key": "key2", "ts": "500"}

    monkeypatch.setattr(vk_api, "call_vk_api", fake_call)
    monkeypatch.setattr(vk_api.httpx, "get", lambda *a, **k: _FakeResponse(payload))
    return lp, server_calls


def test_failed_1_updates_ts_without_server_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lp, server_calls = _primed_poll(monkeypatch, {"failed": 1, "ts": "99"})
    assert lp.poll() == []
    assert lp._ts == "99"
    assert server_calls == []  # key/server still valid


def test_failed_2_refreshes_key_keeps_ts(monkeypatch: pytest.MonkeyPatch) -> None:
    lp, server_calls = _primed_poll(monkeypatch, {"failed": 2})
    assert lp.poll() == []
    assert server_calls == [True]
    assert lp._key == "key2"
    assert lp._ts == "10"  # ts preserved across key refresh


def test_failed_3_full_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    lp, server_calls = _primed_poll(monkeypatch, {"failed": 3})
    assert lp.poll() == []
    assert server_calls == [True]
    assert lp._ts == "500"  # ts taken from fresh server


def test_normal_update_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"ts": "11", "updates": [[4, 7, 0, 42, 0, "hello"]]}
    lp, _ = _primed_poll(monkeypatch, payload)
    updates = lp.poll()
    assert lp._ts == "11"
    assert len(updates) == 1
    message = updates[0]["object"]["message"]
    assert message["text"] == "hello"
    assert message["peer_id"] == 42
    assert message["from_id"] == 42
