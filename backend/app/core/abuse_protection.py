from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.config import Settings
from app.core.abuse_store import set_ip_block
from app.core.rate_limit import check_rate_limit, get_counter_ttl, get_redis


class RouteTier(str, Enum):
    exempt = "exempt"
    public_read = "public_read"
    auth = "auth"
    expensive = "expensive"
    default = "default"


@dataclass(frozen=True)
class AbuseDecision:
    allowed: bool
    retry_after: int | None = None
    reason: str | None = None


def _is_whitelisted(client_ip: str, settings: Settings) -> bool:
    if not settings.abuse_whitelist_ips:
        return False
    return client_ip in {item.strip() for item in settings.abuse_whitelist_ips.split(",") if item.strip()}


def classify_route(path: str, method: str) -> RouteTier:
    normalized = path.rstrip("/") or "/"

    if normalized.startswith("/api/internal/bot") or normalized.startswith("/api/auth/bot"):
        return RouteTier.exempt

    if normalized.startswith("/api/admin"):
        return RouteTier.exempt

    if normalized in {"/health", "/health/ready"}:
        return RouteTier.exempt

    if normalized.startswith("/api/stats"):
        return RouteTier.public_read

    if normalized.startswith("/api/demo"):
        return RouteTier.public_read

    if normalized.startswith("/api/auth/"):
        return RouteTier.auth

    if method.upper() != "GET" and "/preview" in normalized and normalized.startswith("/api/profiles"):
        return RouteTier.expensive

    if normalized.startswith("/api/sync/refresh"):
        return RouteTier.expensive

    if normalized.startswith("/api/"):
        return RouteTier.default

    return RouteTier.exempt


def _tier_limits(tier: RouteTier, settings: Settings) -> tuple[int, int] | None:
    if tier is RouteTier.exempt:
        return None
    if tier is RouteTier.public_read:
        return settings.abuse_public_limit_per_ip, settings.abuse_public_window_seconds
    if tier is RouteTier.auth:
        return settings.abuse_auth_limit_per_ip, settings.abuse_auth_window_seconds
    if tier is RouteTier.expensive:
        return settings.abuse_expensive_limit_per_ip, settings.abuse_expensive_window_seconds
    return settings.abuse_default_limit_per_ip, settings.abuse_default_window_seconds


def _block_key(client_ip: str) -> str:
    return f"abuse:block:{client_ip}"


def _score_key(client_ip: str) -> str:
    return f"abuse:score:{client_ip}"


def is_client_blocked(client_ip: str) -> tuple[bool, int | None]:
    redis = get_redis()
    if not redis.exists(_block_key(client_ip)):
        return False, None
    ttl = get_counter_ttl(_block_key(client_ip))
    return True, ttl if ttl > 0 else None


def record_abuse_score(client_ip: str, points: int, settings: Settings) -> None:
    redis = get_redis()
    score_key = _score_key(client_ip)
    score = int(redis.incrby(score_key, points))
    if score == points:
        redis.expire(score_key, settings.abuse_score_window_seconds)

    if score >= settings.abuse_severe_block_score_threshold:
        set_ip_block(
            client_ip,
            settings.abuse_severe_block_duration_seconds,
            source="auto",
            reason="abuse_score_severe",
        )
    elif score >= settings.abuse_block_score_threshold:
        set_ip_block(
            client_ip,
            settings.abuse_block_duration_seconds,
            source="auto",
            reason="abuse_score_threshold",
        )


def check_abuse_request(client_ip: str, path: str, method: str, settings: Settings) -> AbuseDecision:
    if not settings.abuse_protection_enabled:
        return AbuseDecision(allowed=True)

    if client_ip == "unknown":
        return AbuseDecision(allowed=True)

    if _is_whitelisted(client_ip, settings):
        return AbuseDecision(allowed=True)

    blocked, retry_after = is_client_blocked(client_ip)
    if blocked:
        record_abuse_score(client_ip, settings.abuse_blocked_retry_score, settings)
        return AbuseDecision(
            allowed=False,
            retry_after=retry_after,
            reason="ip_temporarily_blocked",
        )

    global_key = f"abuse:global:{client_ip}"
    if not check_rate_limit(
        global_key,
        settings.abuse_global_limit_per_ip,
        settings.abuse_global_window_seconds,
    ):
        record_abuse_score(client_ip, settings.abuse_global_violation_score, settings)
        retry_after = get_counter_ttl(global_key) or settings.abuse_global_window_seconds
        return AbuseDecision(
            allowed=False,
            retry_after=retry_after,
            reason="global_rate_limit",
        )

    tier = classify_route(path, method)
    tier_limits = _tier_limits(tier, settings)
    if tier_limits is not None:
        limit, window = tier_limits
        tier_key = f"abuse:tier:{tier.value}:{client_ip}"
        if not check_rate_limit(tier_key, limit, window):
            record_abuse_score(client_ip, settings.abuse_tier_violation_score, settings)
            retry_after = get_counter_ttl(tier_key) or window
            return AbuseDecision(
                allowed=False,
                retry_after=retry_after,
                reason=f"{tier.value}_rate_limit",
            )

    return AbuseDecision(allowed=True)
