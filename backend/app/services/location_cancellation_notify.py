"""Мониторинг отмен ближайшего старта: сообщение админу, когда что-то поменялось.

Отмена — единственный статус площадки, у которого есть срок годности: она
касается одной ближайшей субботы, и узнать о ней надо до неё, а не из
недельной сводки. Поэтому здесь отдельный канал: как только синк увидел
новую отмену (или её снятие), уходит короткое сообщение в админский Telegram.

Молчим, когда менять нечего: тишина здесь — нормальное состояние, и её
ценность в том, что любое сообщение означает реальное изменение. На прогоне
тестов не уходит ничего — за это отвечает `notify_admin`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import get_settings
from app.services.admin_notify import notify_admin

logger = logging.getLogger(__name__)

PLATFORM_TITLES: dict[str, str] = {
    "five_verst": "5 вёрст",
    "s95": "S95",
    "runpark": "RunPark",
    "parkrun": "parkrun",
}


@dataclass(frozen=True)
class CancellationChange:
    """Одно изменение статуса «отмена» у строки локации."""

    platform_code: str
    slug: str
    name: str | None
    cancelled: bool
    reason: str | None = None


def _platform_title(code: str) -> str:
    return PLATFORM_TITLES.get(code, code)


def _location_line(change: CancellationChange, base_url: str) -> str:
    title = change.name or change.slug
    line = f"• {_platform_title(change.platform_code)} · {title}"
    if change.reason:
        line += f"\n  Причина: {change.reason}"
    line += f"\n  {base_url}/locations/{change.slug}"
    return line


def format_cancellation_report(changes: list[CancellationChange], *, base_url: str) -> str:
    cancelled = [item for item in changes if item.cancelled]
    restored = [item for item in changes if not item.cancelled]

    parts: list[str] = []
    if cancelled:
        parts.append("🚫 Отмена ближайшего старта")
        parts.extend(_location_line(item, base_url) for item in cancelled)
    if restored:
        if parts:
            parts.append("")
        parts.append("✅ Отмена снята")
        parts.extend(_location_line(item, base_url) for item in restored)
    return "\n".join(parts)


def notify_cancellation_changes(changes: list[CancellationChange]) -> bool:
    """Отправить одно сообщение на весь набор изменений. False — не отправляли."""

    if not changes:
        return False
    base_url = get_settings().app_base_url.rstrip("/")
    text = format_cancellation_report(changes, base_url=base_url)
    try:
        return notify_admin(text)
    except Exception:
        # Мониторинг не имеет права ронять синк: данные уже записаны, а про
        # неудачную отправку расскажет лог.
        logger.exception("Не удалось отправить уведомление об отмене старта")
        return False
