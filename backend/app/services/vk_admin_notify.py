from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.core.runtime_env import is_test_run
from app.services.vk_client import VK_MESSAGE_LIMIT, send_vk_message

logger = logging.getLogger(__name__)


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

# Сколько РАЗНЫХ поломок показывать. Одна залипшая вещь повторяется в каждом
# прогоне: Плотинка №225 дала 13.09.2026 пять одинаковых строк подряд и заняла
# собой весь раздел. Схлопываем одинаковые в «× N» и режем список, чтобы вторая,
# настоящая ошибка дня доезжала до сводки, а не упиралась в лимит длины.
SUMMARY_PROBLEM_LIMIT = 5


def _problem_lines(problems: list[dict[str, Any]]) -> list[str]:
    """Строки раздела «Что болит»: одинаковые схлопнуты, порядок сохранён."""
    counts: dict[str, int] = {}
    for run in problems:
        errors = run.get("errors") or []
        first_error = errors[0] if errors else "без текста ошибки"
        key = f"{run['pipeline_label']}: {first_error}"
        counts[key] = counts.get(key, 0) + 1

    lines = [
        text if repeats == 1 else f"{text} (× {repeats})"
        for text, repeats in list(counts.items())[:SUMMARY_PROBLEM_LIMIT]
    ]
    hidden = len(counts) - len(lines)
    if hidden > 0:
        lines.append(f"…и ещё {hidden} {_plural(hidden, 'поломка', 'поломки', 'поломок')}")
    return lines


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
        for line in _problem_lines(problems):
            lines.append(f"• {line}")

    if admin_url:
        lines.append("")
        lines.append(f"Подробности: {admin_url}")

    text = "\n".join(lines)
    if len(text) > VK_MESSAGE_LIMIT:
        text = text[: VK_MESSAGE_LIMIT - 20].rstrip() + "\n… (сообщение обрезано)"
    return text
