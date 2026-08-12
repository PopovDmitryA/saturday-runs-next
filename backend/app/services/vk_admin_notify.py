from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.core.runtime_env import is_test_run
from app.services.vk_client import VK_MESSAGE_LIMIT, send_vk_message

logger = logging.getLogger(__name__)


def vk_admin_configured() -> bool:
    settings = get_settings()
    return bool(settings.vk_bot_group_token and settings.vk_admin_user_id)


def send_vk_admin_message(text: str, *, reply_to: int | None = None) -> int | None:
    # Тесты гоняются на стеке с боевым .env, поэтому глушим отправку здесь, а не
    # надеемся на моки в каждом тесте: иначе прогон уходит сообщениями админу.
    if is_test_run():
        logger.info("VK admin notify skipped: test run (%s)", text[:80])
        return None

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


def _plural(count: int, one: str, few: str, many: str) -> str:
    tail_100 = count % 100
    if 11 <= tail_100 <= 14:
        return many
    tail = count % 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def _fmt_number(value: float) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.1f}"
    return f"{int(value):,}".replace(",", " ")


# Сколько счётчиков показывать на платформу — в сводке нужны крупные итоги,
# полный разбор есть в админке.
SUMMARY_METRIC_LIMIT = 5


def format_daily_summary(
    day_label: str,
    platforms: list[dict[str, Any]],
    *,
    problems: list[dict[str, Any]] | None = None,
    admin_url: str | None = None,
) -> str:
    """Одна сводка в сутки: сколько раз запускали и что обновилось.

    Детали по локациям сознательно не выводим — за ними админка «Автообновление».
    """
    lines = [f"📊 Автообновление за {day_label}"]

    if not platforms:
        lines.append("")
        lines.append("Запусков не было.")
    for platform in platforms:
        runs = platform["runs"]
        problem_runs = platform["problems"]
        skipped = platform["skipped"]
        mark = "⚠️" if problem_runs else "✅"
        head = (
            f"{mark} {platform['platform_label']}: "
            f"{runs} {_plural(runs, 'запуск', 'запуска', 'запусков')}"
        )
        if problem_runs:
            head += f", {problem_runs} с ошибками"
        if skipped:
            head += f", пропущено {skipped}"
        lines.append("")
        lines.append(head)
        for metric in platform["metrics"][:SUMMARY_METRIC_LIMIT]:
            lines.append(f"• {metric['label']}: {_fmt_number(metric['value'])}")

    if problems:
        lines.append("")
        lines.append("Что болит:")
        for run in problems:
            first_error = run["errors"][0] if run.get("errors") else "без текста ошибки"
            lines.append(f"• {run['pipeline_label']}: {first_error}")

    if admin_url:
        lines.append("")
        lines.append(f"Подробности: {admin_url}")

    text = "\n".join(lines)
    if len(text) > VK_MESSAGE_LIMIT:
        text = text[: VK_MESSAGE_LIMIT - 20].rstrip() + "\n… (сообщение обрезано)"
    return text
