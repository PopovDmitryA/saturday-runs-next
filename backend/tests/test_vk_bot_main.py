from __future__ import annotations

import pytest

from vk_bot import main
from vk_bot.settings import VkBotSettings

ADMIN_ID = 42


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str]]:
    captured: list[tuple[int, str]] = []

    def fake_send(peer_id: int, text: str, *, reply_to: int | None = None) -> int:
        captured.append((peer_id, text))
        return 1

    monkeypatch.setattr(main, "send_message", fake_send)
    monkeypatch.setattr(
        main, "_settings", lambda: VkBotSettings(vk_admin_user_id=ADMIN_ID)
    )
    return captured


def test_parse_command() -> None:
    assert main._parse_command("/sync s95-latest x") == ("sync", ["s95-latest", "x"])
    assert main._parse_command("  /HELP  ") == ("help", [])
    assert main._parse_command("hello world") == ("hello", ["world"])
    assert main._parse_command("   ") == ("", [])


def test_non_admin_is_rejected(sent: list[tuple[int, str]]) -> None:
    main._handle_message(ADMIN_ID, 999, "/help", {})
    assert sent == [(ADMIN_ID, "Доступ только для администратора.")]


def test_help_command(sent: list[tuple[int, str]]) -> None:
    main._handle_message(ADMIN_ID, ADMIN_ID, "/help", {})
    assert len(sent) == 1
    assert "Бот администратора Saturday Runs" in sent[0][1]
    assert "вёрst" not in sent[0][1]


def test_unknown_command(sent: list[tuple[int, str]]) -> None:
    main._handle_message(ADMIN_ID, ADMIN_ID, "/nope", {})
    assert sent[-1][1] == "Неизвестная команда. /help"


def test_stats_invalid_arg(sent: list[tuple[int, str]]) -> None:
    main._handle_message(ADMIN_ID, ADMIN_ID, "/stats abc", {})
    assert sent[-1][1] == "Использование: /stats [дней]"


def test_sync_no_args_lists_pipelines(
    sent: list[tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        main, "list_pipelines", lambda _settings: [("registry", "5v registry /events/")]
    )
    main._handle_message(ADMIN_ID, ADMIN_ID, "/sync", {})
    assert "/sync registry — 5v registry /events/" in sent[-1][1]
    assert "/sync location <slug>" in sent[-1][1]


def test_sync_enqueue_unknown_pipeline(
    sent: list[tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_enqueue(_settings, name, *, location_slug=None):
        raise ValueError("Неизвестный пайплайн. Доступно: latest, registry")

    monkeypatch.setattr(main, "enqueue_pipeline", fake_enqueue)
    main._handle_message(ADMIN_ID, ADMIN_ID, "/sync bogus", {})
    assert sent[-1][1].startswith("Неизвестный пайплайн")


def test_sync_enqueue_success(
    sent: list[tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        main,
        "enqueue_pipeline",
        lambda _settings, name, *, location_slug=None: f"Поставлена в очередь: {name}",
    )
    main._handle_message(ADMIN_ID, ADMIN_ID, "/sync latest", {})
    assert sent[-1][1] == "Поставлена в очередь: latest"


def test_handle_update_ignores_non_message(sent: list[tuple[int, str]]) -> None:
    main._handle_update({"type": "message_reply", "object": {}})
    assert sent == []


def test_handle_update_routes_message(sent: list[tuple[int, str]]) -> None:
    main._handle_update(
        {
            "type": "message_new",
            "object": {"message": {"id": 1, "peer_id": ADMIN_ID, "text": "/help"}},
        }
    )
    assert len(sent) == 1
    assert "Бот администратора" in sent[0][1]
