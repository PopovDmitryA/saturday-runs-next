import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.abuse_protection import (
    RouteTier,
    check_abuse_request,
    classify_route,
    is_client_blocked,
    record_abuse_score,
)
from app.middleware.abuse_middleware import AbuseProtectionMiddleware


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def abuse_settings() -> Settings:
    return Settings(
        abuse_protection_enabled=True,
        abuse_global_limit_per_ip=5,
        abuse_global_window_seconds=60,
        abuse_public_limit_per_ip=3,
        abuse_public_window_seconds=60,
        abuse_default_limit_per_ip=10,
        abuse_default_window_seconds=60,
        abuse_expensive_limit_per_ip=2,
        abuse_expensive_window_seconds=60,
        abuse_block_score_threshold=10,
        abuse_block_duration_seconds=120,
        abuse_severe_block_score_threshold=100,
        abuse_severe_block_duration_seconds=3600,
        abuse_score_window_seconds=3600,
        abuse_global_violation_score=5,
        abuse_tier_violation_score=5,
        abuse_blocked_retry_score=1,
    )


def test_classify_route_tiers() -> None:
    assert classify_route("/health", "GET") is RouteTier.exempt
    assert classify_route("/api/auth/bot/confirm", "POST") is RouteTier.exempt
    assert classify_route("/api/stats/summary", "GET") is RouteTier.public_read
    assert classify_route("/api/auth/login-request", "POST") is RouteTier.auth
    assert classify_route("/api/auth/oauth/vk/start", "GET") is RouteTier.auth
    assert classify_route("/api/auth/oauth/vk/callback", "GET") is RouteTier.auth
    assert classify_route("/api/auth/me", "GET") is RouteTier.default
    assert classify_route("/api/auth/identities", "GET") is RouteTier.default
    assert classify_route("/api/auth/logout", "POST") is RouteTier.default
    assert classify_route("/api/profiles/s95/preview", "POST") is RouteTier.expensive
    assert classify_route("/api/dashboard", "GET") is RouteTier.default


def test_global_rate_limit_blocks(
    fake_redis: fakeredis.FakeRedis,
    abuse_settings: Settings,
) -> None:
    for _ in range(5):
        decision = check_abuse_request("1.2.3.4", "/api/dashboard", "GET", abuse_settings)
        assert decision.allowed is True

    decision = check_abuse_request("1.2.3.4", "/api/dashboard", "GET", abuse_settings)
    assert decision.allowed is False
    assert decision.reason == "global_rate_limit"


def test_abuse_score_triggers_block(
    fake_redis: fakeredis.FakeRedis,
    abuse_settings: Settings,
) -> None:
    record_abuse_score("9.9.9.9", 10, abuse_settings)
    blocked, retry_after = is_client_blocked("9.9.9.9")
    assert blocked is True
    assert retry_after is not None and retry_after > 0


def test_middleware_returns_429(
    fake_redis: fakeredis.FakeRedis,
    abuse_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.middleware.abuse_middleware.get_settings", lambda: abuse_settings)

    app = FastAPI()
    app.add_middleware(AbuseProtectionMiddleware)

    @app.get("/api/stats/summary")
    def stats_summary() -> dict[str, str]:
        return {"ok": "1"}

    client = TestClient(app)
    for _ in range(3):
        assert client.get("/api/stats/summary").status_code == 200

    response = client.get("/api/stats/summary")
    assert response.status_code == 429
    assert response.headers.get("Retry-After")


def _signed_session(secret: str, session_id: str) -> str:
    from app.core.security import sign_session_id

    return sign_session_id(session_id, secret)


def test_sessions_behind_one_ip_do_not_share_a_bucket(
    fake_redis: fakeredis.FakeRedis,
    abuse_settings: Settings,
) -> None:
    """NAT оператора: один IP, разные сессии — лимит у каждого свой.

    Раньше ведро было общим на IP, и абоненты мобильного оператора выбивали
    друг другу 429 (сайт при этом выглядел разлогиненным).
    """
    first = _signed_session(abuse_settings.app_secret_key, "session-one")
    second = _signed_session(abuse_settings.app_secret_key, "session-two")

    for _ in range(5):
        decision = check_abuse_request(
            "10.0.0.1", "/api/dashboard", "GET", abuse_settings, session_cookie=first
        )
        assert decision.allowed is True

    # Первая сессия исчерпала своё ведро...
    exhausted = check_abuse_request(
        "10.0.0.1", "/api/dashboard", "GET", abuse_settings, session_cookie=first
    )
    assert exhausted.allowed is False

    # ...а сосед по тому же IP не пострадал.
    neighbour = check_abuse_request(
        "10.0.0.1", "/api/dashboard", "GET", abuse_settings, session_cookie=second
    )
    assert neighbour.allowed is True


def test_forged_session_cookie_falls_back_to_ip_limit(
    fake_redis: fakeredis.FakeRedis,
    abuse_settings: Settings,
) -> None:
    """Подделанную куку не принимаем: иначе лимит обходился бы мусором в Cookie."""
    for _ in range(5):
        assert (
            check_abuse_request(
                "10.0.0.2", "/api/dashboard", "GET", abuse_settings, session_cookie="not-a-real-signature"
            ).allowed
            is True
        )

    decision = check_abuse_request(
        "10.0.0.2", "/api/dashboard", "GET", abuse_settings, session_cookie="another-forgery"
    )
    assert decision.allowed is False
    assert decision.reason == "global_rate_limit"


def test_session_violations_do_not_ban_the_shared_ip(
    fake_redis: fakeredis.FakeRedis,
    abuse_settings: Settings,
) -> None:
    """Один шумный залогиненный не должен утащить в бан весь NAT-адрес."""
    cookie = _signed_session(abuse_settings.app_secret_key, "noisy-session")

    for _ in range(30):
        check_abuse_request("10.0.0.3", "/api/dashboard", "GET", abuse_settings, session_cookie=cookie)

    blocked, _retry_after = is_client_blocked("10.0.0.3")
    assert blocked is False
