from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

from app.config import Settings
from app.core.abuse_store import set_ip_block
from app.core.rate_limit import check_rate_limit, get_counter_ttl, get_redis
from app.core.security import verify_signed_session_id


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


def resolve_rate_limit_identity(client_ip: str, session_cookie: str | None, settings: Settings) -> tuple[str, bool]:
    """Кого именно ограничиваем: сессию или IP.

    Мобильные операторы NAT-ят сотни абонентов за один адрес, и при IP-лимитах
    они делят одно ведро на всех — невиновные ловят 429, а сайт выглядит
    разлогиненным. Поэтому запрос с валидно подписанной кукой считаем по сессии.

    Подпись проверяется HMAC локально (без Redis и БД), так что подделать
    идентичность и получить бесконечные вёдра нельзя: без app_secret_key
    подписать чужой session_id не выйдет. Анонимы остаются на лимите по IP.

    Возвращает (ключ, is_session).
    """
    if session_cookie:
        session_id = verify_signed_session_id(session_cookie, settings.app_secret_key)
        if session_id is not None:
            digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
            return f"sess:{digest}", True
    return f"ip:{client_ip}", False


def _is_whitelisted(client_ip: str, settings: Settings) -> bool:
    if not settings.abuse_whitelist_ips:
        return False
    return client_ip in {item.strip() for item in settings.abuse_whitelist_ips.split(",") if item.strip()}


def classify_route(path: str, method: str) -> RouteTier:
    normalized = path.rstrip("/") or "/"
    method_upper = method.upper()

    if normalized.startswith("/api/internal/bot") or normalized.startswith("/api/auth/bot"):
        return RouteTier.exempt

    if normalized.startswith("/api/admin"):
        return RouteTier.exempt

    if normalized in {"/health", "/health/ready"}:
        return RouteTier.exempt

    # SEO-адреса: sitemap, robots и пререндер для роботов. Ответы дешёвые и
    # кэшируются, а вот 429 в адрес Яндекса или Google читается им как «сайт
    # лежит» — и обход останавливается. Общий потолок 180 запросов в минуту с
    # адреса (проверяется до тарифа) при этом остаётся.
    if normalized in {"/sitemap.xml", "/robots.txt"} or normalized.startswith("/__prerender"):
        return RouteTier.exempt

    if normalized.startswith("/api/stats"):
        return RouteTier.public_read

    if normalized.startswith("/api/demo"):
        return RouteTier.public_read

    if normalized.startswith("/api/auth/"):
        # Session reads and low-risk writes should not share the tight OAuth/login bucket.
        if method_upper == "GET" and normalized in {
            "/api/auth/me",
            "/api/auth/identities",
            "/api/auth/merge/preview",
            "/api/auth/oauth/vk/redirect-uri",
        }:
            return RouteTier.default
        if method_upper == "GET" and normalized.startswith("/api/auth/login-request/") and normalized.endswith("/status"):
            return RouteTier.default
        if method_upper in {"POST", "PATCH"} and normalized in {"/api/auth/logout", "/api/auth/me"}:
            return RouteTier.default
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


def check_abuse_request(
    client_ip: str,
    path: str,
    method: str,
    settings: Settings,
    session_cookie: str | None = None,
) -> AbuseDecision:
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

    identity, is_session = resolve_rate_limit_identity(client_ip, session_cookie, settings)

    # Очки блокировки копим только на анонимном (IP) трафике. Иначе один
    # злоупотребляющий залогиненный пользователь утащил бы в 15-минутный бан
    # весь NAT оператора — ровно та беда, от которой мы уходим.
    def penalize(score: int) -> None:
        if not is_session:
            record_abuse_score(client_ip, score, settings)

    global_key = f"abuse:global:{identity}"
    if not check_rate_limit(
        global_key,
        settings.abuse_global_limit_per_ip,
        settings.abuse_global_window_seconds,
    ):
        penalize(settings.abuse_global_violation_score)
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
        tier_key = f"abuse:tier:{tier.value}:{identity}"
        if not check_rate_limit(tier_key, limit, window):
            penalize(settings.abuse_tier_violation_score)
            retry_after = get_counter_ttl(tier_key) or window
            return AbuseDecision(
                allowed=False,
                retry_after=retry_after,
                reason=f"{tier.value}_rate_limit",
            )

    return AbuseDecision(allowed=True)
