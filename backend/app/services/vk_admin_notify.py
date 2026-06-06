from __future__ import annotations

import logging
import random
from typing import Any

import httpx

from app.config import get_settings
from app.services.sync_report_labels import field_label, format_field_value, pipeline_label

logger = logging.getLogger(__name__)

VK_API_VERSION = "5.199"


def vk_admin_configured() -> bool:
    settings = get_settings()
    return bool(settings.vk_bot_group_token and settings.vk_admin_user_id)


def send_vk_admin_message(text: str, *, reply_to: int | None = None) -> int | None:
    settings = get_settings()
    if not settings.vk_bot_group_token or not settings.vk_admin_user_id:
        logger.info("VK admin notify skipped: token or admin user id not configured")
        return None

    payload: dict[str, object] = {
        "access_token": settings.vk_bot_group_token,
        "v": VK_API_VERSION,
        "peer_id": settings.vk_admin_user_id,
        "message": text[:4096],
        "random_id": random.randint(1, 2_000_000_000),
    }
    if reply_to is not None:
        payload["reply_to"] = reply_to
    try:
        response = httpx.post(
            "https://api.vk.com/method/messages.send",
            data=payload,
            timeout=30.0,
        )
        response.raise_for_status()
        body = response.json()
        if "error" in body:
            logger.warning("VK messages.send error: %s", body["error"])
            return None
        message_id = body.get("response")
        return int(message_id) if message_id is not None else None
    except Exception:
        logger.exception("Failed to send VK admin message")
        return None


def _fmt_errors(errors: list[str] | None, *, limit: int = 5) -> str:
    if not errors:
        return "нет"
    shown = errors[:limit]
    lines = "\n".join(f"• {item}" for item in shown)
    if len(errors) > limit:
        lines += f"\n… и ещё {len(errors) - limit}"
    return lines


def format_sync_started(pipeline: str, *, details: str | None = None) -> str:
    text = f"▶️ Запуск: {pipeline_label(pipeline)}"
    if details:
        text += f"\n{details}"
    return text


def format_sync_finished(pipeline: str, payload: dict[str, Any]) -> str:
    errors = payload.get("errors")
    error_count = len(errors) if isinstance(errors, list) else 0
    status = "✅" if error_count == 0 else "⚠️"
    lines = [f"{status} Завершено: {pipeline_label(pipeline)}"]

    skip = {"errors"}
    for key, value in payload.items():
        if key in skip or value is None:
            continue
        if isinstance(value, list) and not value:
            continue
        if isinstance(value, (int, float)) and value == 0 and key not in {"rotation_index"}:
            continue
        lines.append(f"{field_label(key)}: {format_field_value(key, value)}")

    if error_count:
        lines.append(f"ошибок: {error_count}")
        lines.append(_fmt_errors(errors if isinstance(errors, list) else None))
    return "\n".join(lines)


def notify_sync_started(pipeline: str, *, details: str | None = None) -> None:
    send_vk_admin_message(format_sync_started(pipeline, details=details))


def notify_sync_finished(pipeline: str, payload: dict[str, Any]) -> None:
    send_vk_admin_message(format_sync_finished(pipeline, payload))
