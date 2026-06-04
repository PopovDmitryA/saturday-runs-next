from __future__ import annotations

import json
import logging
from typing import Any

import redis

from app.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

BROADCAST_AWAITING_PREFIX = "bot:broadcast:awaiting:"
BROADCAST_DRAFT_PREFIX = "bot:broadcast:draft:"
BROADCAST_TTL_SECONDS = 3600


def _awaiting_key(telegram_id: int) -> str:
    return f"{BROADCAST_AWAITING_PREFIX}{telegram_id}"


def _draft_key(telegram_id: int) -> str:
    return f"{BROADCAST_DRAFT_PREFIX}{telegram_id}"


def start_broadcast_compose(redis_client: redis.Redis | None, telegram_id: int) -> None:
    client = redis_client or get_redis_client()
    client.delete(_draft_key(telegram_id))
    client.setex(_awaiting_key(telegram_id), BROADCAST_TTL_SECONDS, "1")


def save_broadcast_draft(redis_client: redis.Redis | None, telegram_id: int, text: str) -> None:
    client = redis_client or get_redis_client()
    client.delete(_awaiting_key(telegram_id))
    payload = json.dumps({"text": text}, ensure_ascii=False)
    client.setex(_draft_key(telegram_id), BROADCAST_TTL_SECONDS, payload)


def get_broadcast_draft(redis_client: redis.Redis | None, telegram_id: int) -> str | None:
    client = redis_client or get_redis_client()
    raw = client.get(_draft_key(telegram_id))
    if not raw:
        return None
    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid broadcast draft payload for telegram_id=%s", telegram_id)
        return None
    text = data.get("text")
    return text if isinstance(text, str) and text.strip() else None


def is_awaiting_broadcast_text(redis_client: redis.Redis | None, telegram_id: int) -> bool:
    client = redis_client or get_redis_client()
    return client.get(_awaiting_key(telegram_id)) is not None


def clear_broadcast_state(redis_client: redis.Redis | None, telegram_id: int) -> None:
    client = redis_client or get_redis_client()
    client.delete(_awaiting_key(telegram_id), _draft_key(telegram_id))
