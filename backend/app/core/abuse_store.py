from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.rate_limit import get_redis

IPV4_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)$"
)


def _block_key(client_ip: str) -> str:
    return f"abuse:block:{client_ip}"


def _score_key(client_ip: str) -> str:
    return f"abuse:score:{client_ip}"


def _meta_key(client_ip: str) -> str:
    return f"abuse:meta:{client_ip}"


def _telegram_ban_key(telegram_id: int) -> str:
    return f"abuse:ban:user:{telegram_id}"


def _telegram_meta_key(telegram_id: int) -> str:
    return f"abuse:ban:user:meta:{telegram_id}"


def _user_ip_key(telegram_id: int) -> str:
    return f"abuse:user:ip:{telegram_id}"


@dataclass(frozen=True)
class IpBlockRecord:
    ip: str
    ttl_seconds: int | None
    score: int
    source: str
    reason: str
    created_at: datetime | None
    created_by: str | None


@dataclass(frozen=True)
class TelegramBanRecord:
    telegram_id: int
    ttl_seconds: int | None
    source: str
    reason: str
    created_at: datetime | None
    created_by: str | None
    last_ip: str | None


def is_valid_ip(value: str) -> bool:
    return bool(IPV4_RE.match(value.strip()))


def remember_user_ip(telegram_id: int, client_ip: str) -> None:
    if not client_ip or client_ip == "unknown":
        return
    redis = get_redis()
    redis.setex(_user_ip_key(telegram_id), 30 * 86400, client_ip)


def get_user_last_ip(telegram_id: int) -> str | None:
    value = get_redis().get(_user_ip_key(telegram_id))
    return value if value else None


def get_ip_abuse_score(client_ip: str) -> int:
    value = get_redis().get(_score_key(client_ip))
    return int(value) if value else 0


def clear_ip_abuse_score(client_ip: str) -> None:
    get_redis().delete(_score_key(client_ip))


def set_ip_block(
    client_ip: str,
    duration_seconds: int,
    *,
    source: str,
    reason: str,
    created_by: str | None = None,
) -> None:
    redis = get_redis()
    redis.setex(_block_key(client_ip), duration_seconds, "1")
    meta = {
        "source": source,
        "reason": reason,
        "created_by": created_by or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    redis.set(_meta_key(client_ip), json.dumps(meta, ensure_ascii=False))
    redis.expire(_meta_key(client_ip), duration_seconds)


def remove_ip_block(client_ip: str) -> bool:
    redis = get_redis()
    deleted = int(redis.delete(_block_key(client_ip), _meta_key(client_ip)))
    return deleted > 0


def get_ip_block_record(client_ip: str) -> IpBlockRecord | None:
    redis = get_redis()
    if not redis.exists(_block_key(client_ip)):
        return None
    ttl = redis.ttl(_block_key(client_ip))
    ttl_seconds = int(ttl) if ttl and ttl > 0 else None
    meta = _read_meta(redis.get(_meta_key(client_ip)))
    return IpBlockRecord(
        ip=client_ip,
        ttl_seconds=ttl_seconds,
        score=get_ip_abuse_score(client_ip),
        source=meta.get("source") or "auto",
        reason=meta.get("reason") or "",
        created_at=_parse_iso(meta.get("created_at")),
        created_by=meta.get("created_by") or None,
    )


def list_ip_blocks() -> list[IpBlockRecord]:
    redis = get_redis()
    records: list[IpBlockRecord] = []
    cursor = 0
    while True:
        cursor, keys = redis.scan(cursor, match="abuse:block:*", count=200)
        for key in keys:
            ip = key.removeprefix("abuse:block:")
            record = get_ip_block_record(ip)
            if record is not None:
                records.append(record)
        if cursor == 0:
            break
    records.sort(key=lambda item: item.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return records


def set_telegram_ban(
    telegram_id: int,
    duration_seconds: int,
    *,
    source: str,
    reason: str,
    created_by: str | None = None,
) -> None:
    redis = get_redis()
    redis.setex(_telegram_ban_key(telegram_id), duration_seconds, "1")
    meta = {
        "source": source,
        "reason": reason,
        "created_by": created_by or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    redis.set(_telegram_meta_key(telegram_id), json.dumps(meta, ensure_ascii=False))
    redis.expire(_telegram_meta_key(telegram_id), duration_seconds)


def remove_telegram_ban(telegram_id: int) -> bool:
    redis = get_redis()
    deleted = int(redis.delete(_telegram_ban_key(telegram_id), _telegram_meta_key(telegram_id)))
    return deleted > 0


def is_telegram_banned(telegram_id: int) -> bool:
    return bool(get_redis().exists(_telegram_ban_key(telegram_id)))


def get_telegram_ban_record(telegram_id: int) -> TelegramBanRecord | None:
    redis = get_redis()
    if not redis.exists(_telegram_ban_key(telegram_id)):
        return None
    ttl = redis.ttl(_telegram_ban_key(telegram_id))
    ttl_seconds = int(ttl) if ttl and ttl > 0 else None
    meta = _read_meta(redis.get(_telegram_meta_key(telegram_id)))
    return TelegramBanRecord(
        telegram_id=telegram_id,
        ttl_seconds=ttl_seconds,
        source=meta.get("source") or "manual",
        reason=meta.get("reason") or "",
        created_at=_parse_iso(meta.get("created_at")),
        created_by=meta.get("created_by") or None,
        last_ip=get_user_last_ip(telegram_id),
    )


def list_telegram_bans() -> list[TelegramBanRecord]:
    redis = get_redis()
    records: list[TelegramBanRecord] = []
    cursor = 0
    while True:
        cursor, keys = redis.scan(cursor, match="abuse:ban:user:*", count=200)
        for key in keys:
            if ":meta:" in key:
                continue
            suffix = key.removeprefix("abuse:ban:user:")
            if not suffix.isdigit():
                continue
            record = get_telegram_ban_record(int(suffix))
            if record is not None:
                records.append(record)
        if cursor == 0:
            break
    records.sort(key=lambda item: item.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return records


def _read_meta(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return {str(key): str(value) for key, value in data.items()}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
