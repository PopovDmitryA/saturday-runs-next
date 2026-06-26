from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.services.sync_report_labels import (
    DETAIL_LIST_KEYS,
    field_label,
    format_detail_sections,
    format_field_value,
    pipeline_label,
)
from app.services.vk_client import VK_MESSAGE_LIMIT, send_vk_message

logger = logging.getLogger(__name__)


def vk_admin_configured() -> bool:
    settings = get_settings()
    return bool(settings.vk_bot_group_token and settings.vk_admin_user_id)


def send_vk_admin_message(text: str, *, reply_to: int | None = None) -> int | None:
    settings = get_settings()
    if not settings.vk_bot_group_token or not settings.vk_admin_user_id:
        logger.info("VK admin notify skipped: token or admin user id not configured")
        return None

    try:
        result_id = send_vk_message(
            settings.vk_bot_group_token,
            settings.vk_admin_user_id,
            text,
            reply_to=reply_to,
            timeout=30.0,
        )
        logger.info("VK admin message sent (message_id=%s, len=%d)", result_id, len(text))
        return result_id
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

    skip = {"errors", *DETAIL_LIST_KEYS}
    for key, value in payload.items():
        if key in skip or value is None:
            continue
        if isinstance(value, list) and not value:
            continue
        if isinstance(value, (int, float)) and value == 0 and key not in {"rotation_index"}:
            continue
        lines.append(f"{field_label(key)}: {format_field_value(key, value)}")

    lines.extend(format_detail_sections(payload))

    if error_count:
        lines.append(f"ошибок: {error_count}")
        lines.append(_fmt_errors(errors if isinstance(errors, list) else None))

    text = "\n".join(lines)
    if len(text) > VK_MESSAGE_LIMIT:
        text = text[: VK_MESSAGE_LIMIT - 20].rstrip() + "\n… (сообщение обрезано)"
    return text


def notify_sync_started(pipeline: str, *, details: str | None = None) -> None:
    send_vk_admin_message(format_sync_started(pipeline, details=details))


def notify_sync_finished(pipeline: str, payload: dict[str, Any]) -> None:
    send_vk_admin_message(format_sync_finished(pipeline, payload))
