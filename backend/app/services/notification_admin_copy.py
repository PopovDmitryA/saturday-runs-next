"""Копии уведомлений в админский Telegram — одним сообщением на рассылку.

Рассылка (отмены стартов, новая карточка бэклога) уходит десяткам людей
одним и тем же текстом, и копия «кому ушло» на каждого засыпала Дмитрия
одинаковыми сообщениями. Поэтому копия не шлётся сразу, а кладётся в Redis
в группу по тексту сообщения. Beat-задача `notifications.flush_admin_copies`
раз в минуту отправляет группы, в которые никто не добавлялся QUIET_SECONDS
(рассылка закончилась), или которые копятся дольше MAX_WAIT_SECONDS (длинная
рассылка — не держим до бесконечности). Итог — одно сообщение: список
получателей и ниже сам текст ровно в том виде, в каком его увидели люди.

Redis недоступен — копия уходит сразу, как раньше: лучше дубль, чем ничего.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import cast

from app.config import get_settings
from app.notification_markup import to_telegram_html
from app.services.notification_senders import send_telegram_html

logger = logging.getLogger(__name__)

# Тишина в группе, после которой рассылку считаем законченной. Воркер шлёт
# доставки одну за другой за секунды — минуты с запасом.
QUIET_SECONDS = 60
# Потолок ожидания: рассылка на сотни человек (или повторы подметальщика) не
# должна задерживать копию дольше этого.
MAX_WAIT_SECONDS = 10 * 60
# Группы в Redis живут не дольше суток, даже если beat лёг.
GROUP_TTL_SECONDS = 24 * 3600
# Telegram режет сообщения длиннее 4096 символов; HTML длиннее видимого
# текста, так что считаем по нему — с запасом.
TELEGRAM_LIMIT = 4000
MIN_LIST_BUDGET = 300

KEY_PREFIX = "notifications:admin_copy"
GROUPS_KEY = f"{KEY_PREFIX}:groups"


def _keys(group: str) -> tuple[str, str, str]:
    base = f"{KEY_PREFIX}:{group}"
    return f"{base}:to", f"{base}:body", f"{base}:first"


def group_id(body_html: str) -> str:
    return hashlib.sha1(body_html.encode()).hexdigest()[:20]


def _recipients_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "получателю"
    return "получателям"


def summary_html(recipients: list[str], body_html: str) -> str:
    """Шапка со списком «кому» и ниже текст сообщения. Один получатель —
    прежний вид в одну строку."""
    if len(recipients) == 1:
        return f"📨 Сообщение направлено {recipients[0]}\n\n{body_html}"
    budget = max(TELEGRAM_LIMIT - len(body_html), MIN_LIST_BUDGET)
    lines: list[str] = []
    used = 0
    for index, recipient in enumerate(recipients):
        line = f"• {recipient}"
        if used + len(line) + 1 > budget:
            lines.append(f"… и ещё {len(recipients) - index}")
            break
        lines.append(line)
        used += len(line) + 1
    head = f"📨 Сообщение направлено {len(recipients)} {_recipients_word(len(recipients))}:"
    return f"{head}\n" + "\n".join(lines) + f"\n\n{body_html}"


def _send(html: str) -> bool:
    settings = get_settings()
    try:
        outcome = send_telegram_html(str(settings.telegram_admin_chat_id), html)
    except Exception:  # noqa: BLE001 — копия админу не важнее самой доставки
        logger.exception("notify: admin copy failed")
        return False
    if not outcome.ok:
        logger.warning("notify: admin copy failed: %s", outcome.error)
    return outcome.ok


def add_copy(recipient: str, channel_title: str, body_html: str, *, now: float | None = None) -> None:
    """Положить копию в группу. recipient — подпись «кому» как есть, экранируем здесь."""
    entry = f"{to_telegram_html(recipient)} · {channel_title}"
    now = time.time() if now is None else now
    group = group_id(body_html)
    to_key, body_key, first_key = _keys(group)
    try:
        from app.core.redis_client import get_redis_client

        pipe = get_redis_client().pipeline(transaction=True)
        pipe.set(body_key, body_html, nx=True, ex=GROUP_TTL_SECONDS)
        pipe.set(first_key, str(now), nx=True, ex=GROUP_TTL_SECONDS)
        pipe.rpush(to_key, entry)
        pipe.expire(to_key, GROUP_TTL_SECONDS)
        pipe.zadd(GROUPS_KEY, {group: now})
        pipe.execute()
    except Exception:  # noqa: BLE001 — без Redis шлём сразу, по-старому
        logger.exception("notify: admin copy buffer unavailable, sending directly")
        _send(summary_html([entry], body_html))


def _take(group: str) -> tuple[list[str], str | None]:
    """Забрать группу целиком и атомарно: параллельный flush получит пусто,
    а копия, пришедшая следом, заведёт новую группу."""
    from app.core.redis_client import get_redis_client

    to_key, body_key, first_key = _keys(group)
    pipe = get_redis_client().pipeline(transaction=True)
    pipe.lrange(to_key, 0, -1)
    pipe.get(body_key)
    pipe.delete(to_key, body_key, first_key)
    pipe.zrem(GROUPS_KEY, group)
    recipients, body, _, _ = pipe.execute()
    return list(recipients or []), body


def flush_ready(*, now: float | None = None, force: bool = False) -> int:
    """Отправить готовые группы. force — все сразу (тесты, ручной прогон).
    Возвращает число отправленных сводок."""
    from app.core.redis_client import get_redis_client

    now = time.time() if now is None else now
    client = get_redis_client()
    sent = 0
    groups = cast(list[tuple[str, float]], client.zrange(GROUPS_KEY, 0, -1, withscores=True))
    for group, last_added in groups:
        if not force and last_added > now - QUIET_SECONDS:
            first = cast(str | None, client.get(_keys(group)[2]))
            # Без отметки начала (истёк TTL) группу не держим — забираем.
            if first is not None and float(first) > now - MAX_WAIT_SECONDS:
                continue
        recipients, body = _take(group)
        if not recipients or body is None:
            continue
        sent += int(_send(summary_html(recipients, body)))
    return sent
