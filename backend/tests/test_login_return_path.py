"""Возврат на исходную страницу после входа: ?next= у /login доезжает до любого способа входа.

Адрес приходит от клиента, поэтому половина тестов — про то, что чужой адрес
(открытый редирект) не проходит ни на одном пути, а вход при этом не ломается и
ведёт туда же, куда вёл раньше.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Generator
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routes.auth import _oauth_success_response
from app.auth.providers.base import OAuthProfile
from app.config import Settings, get_settings
from app.core.return_path import MAX_RETURN_PATH_LENGTH, sanitize_return_path
from app.db.session import get_db
from app.main import app
from app.models import User
from app.services.onboarding_service import complete_onboarding, post_login_redirect_target

BOT_HEADERS = {"X-Bot-Secret": "bot-secret"}
WIDGET_BOT_TOKEN = "123456789:AAExampleTokenForTestsOnly"
PAGE = "/locations/bitsa"
PAGE_ENCODED = "%2Flocations%2Fbitsa"


@pytest.fixture
def auth_settings() -> Settings:
    return Settings(
        app_secret_key="test-secret-key",
        app_debug=True,
        app_base_url="http://testserver",
        telegram_bot_internal_secret="bot-secret",
        telegram_bot_username="TestBot",
        telegram_login_enabled=True,
        telegram_login_bot_token=WIDGET_BOT_TOKEN,
        yandex_oauth_client_id="yandex-client",
        yandex_oauth_redirect_uri="",
        email_login_enabled=True,
        smtp_enabled=True,
        smtp_host="smtp.timeweb.ru",
        smtp_user="support@run5k.run",
        smtp_password="secret",
        ip_geo_lookup_enabled=False,
        database_url=get_settings().database_url,
        redis_url="redis://localhost:6379/0",
    )


@pytest.fixture
def client(
    db_session: Session, fake_redis: fakeredis.FakeRedis, auth_settings: Settings
) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: auth_settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --- Правила адреса ---------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/locations/bitsa", "/locations/bitsa"),
        ("/", "/"),
        ("/ratings/fastest?gender=f&page=2#top", "/ratings/fastest?gender=f&page=2#top"),
        ("/users/12/runs#2026", "/users/12/runs#2026"),
        # Кириллица в фильтре — кодируется, а не отбрасывается.
        ("/locations?q=Москва", "/locations?q=%D0%9C%D0%BE%D1%81%D0%BA%D0%B2%D0%B0"),
        # Уже закодированное не кодируется второй раз.
        ("/locations?q=%D0%9C", "/locations?q=%D0%9C"),
        # Всё, что можно прочитать двояко в HTML или заголовке, — процентами.
        ('/x?a="<b>"&c=d e', "/x?a=%22%3Cb%3E%22&c=d%20e"),
        ("  /locations/bitsa  ", "/locations/bitsa"),
        # Похоже на служебное, но не оно.
        ("/loginator", "/loginator"),
        ("/locations/login", "/locations/login"),
        # Точки внутри имён и в строке запроса — не сегменты пути.
        ("/a..b/c", "/a..b/c"),
        ("/ratings?q=../x#..", "/ratings?q=../x#.."),
    ],
)
def test_sanitizer_keeps_paths_inside_the_site(raw: str, expected: str) -> None:
    assert sanitize_return_path(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "//evil.com",
        "//evil.com/locations",
        "/\\evil.com",
        "/locations\\..\\x",
        "https://evil.com/",
        "http:/evil.com",
        "javascript:alert(1)",
        "locations/bitsa",
        "/locations\nSet-Cookie: x=1",
        "/loc\rations",
        "/locations\x00",
        "/locations\x7f",
        "/\tevil",
        "/login",
        "/login?next=/locations",
        "/LOGIN",
        "/%6Cogin",
        "/api/auth/me",
        "/oauth/vk/callback",
        "/auth/telegram/return?state=x",
        "/render/og/default",
        "/welcome",
        "/welcome?next=/locations",
        "",
        "   ",
        "/" + "a" * MAX_RETURN_PATH_LENGTH,
        # Влезает до кодирования, но не после: кириллица втрое длиннее.
        "/" + "ж" * (MAX_RETURN_PATH_LENGTH // 2),
        # Сегменты «.» и «..» браузер схлопывает: «/x/..//evil.com» он читает
        # как «//evil.com» — адрес чужого сайта (находка ревью 12.10.2026).
        "/.//evil.com",
        "/a/..//evil.com",
        "/locations/..//evil.com",
        "/%2e//evil.com",
        "/x/%2E%2E//evil.com?a=1#h",
        "/x/../login",
        "/%2e%2e/api/auth/me",
        "/locations/.",
        "/locations/..",
        # Закодированный двойной слеш — тоже не путь сайта.
        "/%2F%2Fevil.com",
    ],
)
def test_sanitizer_rejects_everything_else(raw: str) -> None:
    assert sanitize_return_path(raw) is None


@pytest.mark.parametrize("raw", [None, 42, ["/locations"], {"next": "/"}, b"/locations"])
def test_sanitizer_rejects_non_strings(raw: object) -> None:
    assert sanitize_return_path(raw) is None


# --- Куда ведёт сервер ------------------------------------------------------


def _new_user(db: Session) -> User:
    user = User(display_name="Бегун", consent_accepted=True)
    db.add(user)
    db.commit()
    return user


def test_target_for_newcomer_goes_through_onboarding(db_session: Session) -> None:
    user = _new_user(db_session)
    assert post_login_redirect_target(db_session, user.id) == "welcome"
    assert post_login_redirect_target(db_session, user.id, PAGE) == f"welcome?next={PAGE_ENCODED}"
    assert post_login_redirect_target(db_session, user.id, "/?q=1#x") == "welcome?next=%2F%3Fq%3D1%23x"
    # Чужой адрес — как будто его не было.
    assert post_login_redirect_target(db_session, user.id, "//evil.com") == "welcome"


def test_target_for_regular_user_is_the_page(db_session: Session) -> None:
    user = _new_user(db_session)
    complete_onboarding(db_session, user)
    assert post_login_redirect_target(db_session, user.id) == "dashboard"
    assert post_login_redirect_target(db_session, user.id, None) == "dashboard"
    assert post_login_redirect_target(db_session, user.id, PAGE) == "locations/bitsa"
    assert post_login_redirect_target(db_session, user.id, "/ratings?gender=f#top") == "ratings?gender=f#top"
    # Главная: клиенты дописывают «/» сами, пустая строка и есть корень.
    assert post_login_redirect_target(db_session, user.id, "/") == ""
    assert post_login_redirect_target(db_session, user.id, "https://evil.com") == "dashboard"
    assert post_login_redirect_target(db_session, user.id, "/login") == "dashboard"


def test_target_for_missing_user_keeps_the_page(db_session: Session) -> None:
    assert post_login_redirect_target(db_session, uuid4(), PAGE) == "locations/bitsa"
    assert post_login_redirect_target(db_session, uuid4()) == "dashboard"


# --- Вход через бота ----------------------------------------------------------


def _start_bot_login(client: TestClient, *, next_path: str | None = None, **params: str) -> str:
    query = {"consent": "true", **params}
    if next_path is not None:
        query["next"] = next_path
    response = client.post("/api/auth/login-request", params=query)
    assert response.status_code == 200
    return str(response.json()["request_token"])


def _confirm_in_bot(client: TestClient, token: str, telegram_id: int) -> str:
    response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": token,
            "telegram_id": telegram_id,
            "telegram_username": "runner",
            "telegram_chat_id": telegram_id,
            "consent_accepted": True,
        },
        headers=BOT_HEADERS,
    )
    assert response.status_code == 200
    return str(response.json()["magic_link"])


def _magic_location(client: TestClient, magic_link: str) -> str:
    token = magic_link.split("token=")[1]
    response = client.get("/api/auth/callback", params={"token": token}, follow_redirects=False)
    assert response.status_code == 302
    return str(response.headers["location"])


def _bot_session(client: TestClient, telegram_id: int) -> str:
    """Войти через бота без адреса возврата и закончить онбординг — «бывалый» человек."""
    token = _start_bot_login(client)
    _confirm_in_bot(client, token, telegram_id)
    claim = client.post(f"/api/auth/login-request/{token}/claim")
    assert claim.status_code == 200
    session = str(claim.cookies["sr_session"])
    done = client.post("/api/auth/onboarding/complete", cookies={"sr_session": session})
    assert done.status_code == 200
    return session


def test_bot_login_returns_regular_user_to_the_page(
    client: TestClient, fake_redis: fakeredis.FakeRedis, auth_settings: Settings
) -> None:
    telegram_id = 551000001
    _bot_session(client, telegram_id)
    client.cookies.clear()

    token = _start_bot_login(client, next_path=PAGE)
    assert fake_redis.get(f"login_req_next:{token}") == PAGE
    magic_link = _confirm_in_bot(client, token, telegram_id)
    # Адрес доживает до конца ссылки-страховки, а не заявки.
    ttl = fake_redis.ttl(f"login_req_next:{token}")
    assert 0 < ttl <= auth_settings.magic_link_ttl_seconds

    claim = client.post(f"/api/auth/login-request/{token}/claim")
    assert claim.status_code == 200
    assert claim.json()["redirect"] == "locations/bitsa"

    # Claim адрес не стирает: ссылкой из бота после него входят туда же.
    assert fake_redis.get(f"login_req_next:{token}") == PAGE
    assert _magic_location(client, magic_link) == "http://testserver/locations/bitsa"


def test_bot_login_sends_newcomer_to_onboarding_with_the_page(client: TestClient) -> None:
    token = _start_bot_login(client, next_path=PAGE)
    magic_link = _confirm_in_bot(client, token, 551000002)

    claim = client.post(f"/api/auth/login-request/{token}/claim")
    assert claim.json()["redirect"] == f"welcome?next={PAGE_ENCODED}"
    assert _magic_location(client, magic_link) == f"http://testserver/welcome?next={PAGE_ENCODED}"


@pytest.mark.parametrize(
    "bad_next", ["//evil.com", "https://evil.com/x", "/api/auth/me", "/login", "/x/..//evil.com"]
)
def test_bot_login_ignores_foreign_next(
    client: TestClient, fake_redis: fakeredis.FakeRedis, bad_next: str
) -> None:
    token = _start_bot_login(client, next_path=bad_next)
    assert fake_redis.get(f"login_req_next:{token}") is None
    magic_link = _confirm_in_bot(client, token, 551000003)

    claim = client.post(f"/api/auth/login-request/{token}/claim")
    assert claim.json()["redirect"] == "welcome"
    assert _magic_location(client, magic_link) == "http://testserver/welcome"


def test_bot_link_mode_does_not_store_next(client: TestClient, fake_redis: fakeredis.FakeRedis) -> None:
    session = _bot_session(client, 551000004)
    response = client.post(
        "/api/auth/login-request",
        params={"link": "true", "next": PAGE},
        cookies={"sr_session": session},
    )
    assert response.status_code == 200
    assert fake_redis.get(f"login_req_next:{response.json()['request_token']}") is None


def test_bot_deny_forgets_next(client: TestClient, fake_redis: fakeredis.FakeRedis) -> None:
    token = _start_bot_login(client, next_path=PAGE)
    deny = client.post(
        "/api/auth/bot/deny",
        json={"request_token": token, "telegram_id": 551000005},
        headers=BOT_HEADERS,
    )
    assert deny.status_code == 200
    assert fake_redis.get(f"login_req_next:{token}") is None


# --- VK и Яндекс ---------------------------------------------------------------


def _start_yandex(client: TestClient, *, next_path: str | None = None, **params: str) -> str:
    query = {"consent": "true", **params}
    if next_path is not None:
        query["next"] = next_path
    response = client.get("/api/auth/oauth/yandex/start", params=query, follow_redirects=False)
    assert response.status_code == 302
    location = str(response.headers["location"])
    assert location.startswith("https://oauth.yandex.ru/")
    return parse_qs(urlsplit(location).query)["state"][0]


@pytest.fixture
def yandex_profile(monkeypatch: pytest.MonkeyPatch) -> OAuthProfile:
    """Обмен кода у Яндекса подменён: в сеть тесты не ходят."""
    profile = OAuthProfile(
        external_id=f"ya-{uuid4().hex[:12]}",
        display_name="Бегун Яндексов",
        email=None,
        profile_json={},
    )
    monkeypatch.setattr(
        "app.services.oauth_service.yandex_provider.yandex_exchange_code",
        lambda settings, code: profile,
    )
    return profile


def test_oauth_start_keeps_next_in_state(client: TestClient, fake_redis: fakeredis.FakeRedis) -> None:
    state = _start_yandex(client, next_path=PAGE)
    payload = json.loads(fake_redis.get(f"oauth_state:{state}"))
    assert payload["return_path"] == PAGE

    plain = _start_yandex(client)
    assert "return_path" not in json.loads(fake_redis.get(f"oauth_state:{plain}"))

    foreign = _start_yandex(client, next_path="//evil.com")
    assert "return_path" not in json.loads(fake_redis.get(f"oauth_state:{foreign}"))


def test_oauth_link_mode_ignores_next(client: TestClient, fake_redis: fakeredis.FakeRedis) -> None:
    session = _bot_session(client, 551000010)
    response = client.get(
        "/api/auth/oauth/yandex/start",
        params={"mode": "link", "next": PAGE},
        cookies={"sr_session": session},
        follow_redirects=False,
    )
    assert response.status_code == 302
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    assert "return_path" not in json.loads(fake_redis.get(f"oauth_state:{state}"))


def test_oauth_finish_sends_newcomer_to_onboarding_with_the_page(
    client: TestClient, yandex_profile: OAuthProfile
) -> None:
    state = _start_yandex(client, next_path=PAGE)
    response = client.post("/api/auth/oauth/yandex/finish", json={"code": "code-1", "state": state})
    assert response.status_code == 200
    assert response.json()["redirect"] == f"welcome?next={PAGE_ENCODED}"


def test_oauth_finish_returns_regular_user_to_the_page(
    client: TestClient, db_session: Session, yandex_profile: OAuthProfile
) -> None:
    first = client.post(
        "/api/auth/oauth/yandex/finish", json={"code": "code-1", "state": _start_yandex(client)}
    )
    assert first.json()["redirect"] == "welcome"
    done = client.post("/api/auth/onboarding/complete", cookies={"sr_session": first.cookies["sr_session"]})
    assert done.status_code == 200
    client.cookies.clear()

    again = client.post(
        "/api/auth/oauth/yandex/finish",
        json={"code": "code-2", "state": _start_yandex(client, next_path="/ratings/fastest?gender=f#top")},
    )
    assert again.json()["redirect"] == "ratings/fastest?gender=f#top"

    home = client.post(
        "/api/auth/oauth/yandex/finish", json={"code": "code-3", "state": _start_yandex(client, next_path="/")}
    )
    assert home.json()["redirect"] == ""


def test_oauth_get_callback_success_page_leads_to_the_page(
    client: TestClient, yandex_profile: OAuthProfile
) -> None:
    state = _start_yandex(client, next_path=PAGE)
    response = client.get(
        "/api/auth/oauth/yandex/callback", params={"code": "code-1", "state": state}, follow_redirects=False
    )
    assert response.status_code == 200
    assert f"http://testserver/welcome?next={PAGE_ENCODED}" in response.text


def test_oauth_get_callback_error_keeps_next(client: TestClient) -> None:
    state = _start_yandex(client, next_path=PAGE)
    response = client.get(
        "/api/auth/oauth/yandex/callback",
        params={"error": "access_denied", "state": state},
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("http://testserver/login?oauth_error=access_denied")
    assert location.endswith(f"&next={PAGE_ENCODED}")


def test_oauth_get_callback_exchange_failure_keeps_next(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """State уже забран (GETDEL), когда падает обмен кода, — адрес подсмотрен заранее."""

    def broken_exchange(settings: Settings, code: str) -> OAuthProfile:
        raise RuntimeError("yandex is down")

    monkeypatch.setattr("app.services.oauth_service.yandex_provider.yandex_exchange_code", broken_exchange)
    state = _start_yandex(client, next_path=PAGE)
    response = client.get(
        "/api/auth/oauth/yandex/callback", params={"code": "code-1", "state": state}, follow_redirects=False
    )
    assert response.status_code == 302
    location = response.headers["location"]
    assert "/login?oauth_error=" in location
    assert location.endswith(f"&next={PAGE_ENCODED}")


def test_oauth_get_callback_error_without_state_has_no_next(client: TestClient) -> None:
    response = client.get(
        "/api/auth/oauth/yandex/callback", params={"error": "access_denied"}, follow_redirects=False
    )
    assert response.status_code == 302
    assert "next=" not in response.headers["location"]


def test_oauth_success_page_escapes_the_address(auth_settings: Settings) -> None:
    hostile = 'http://testserver/x?a="><script>alert(1)</script>&b=1\u2028'
    page = _oauth_success_response(auth_settings, "signed-session", hostile).body.decode()

    # Ровно один тег скрипта — наш; чужой «</script>» его не закрыл.
    assert page.count("<script>") == 1
    assert page.count("</script>") == 1
    assert "\\u003c/script\\u003e" in page
    assert "\\u0026" in page
    # Разделитель строк U+2028 ломает строковый литерал в старых JS-движках.
    script = page.split("<script>", 1)[1].split("</script>", 1)[0]
    assert "\u2028" not in script
    assert "\\u2028" in script
    # В атрибутах кавычка и скобки — сущностями.
    assert 'content="0;url=http://testserver/x?a=&quot;&gt;&lt;script&gt;' in page
    assert 'href="http://testserver/x?a=&quot;&gt;&lt;script&gt;' in page


# --- Почта --------------------------------------------------------------------


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    letters: list[str] = []

    def fake_queue(to: str, subject: str, text_body: str, html_body: str | None = None) -> bool:
        letters.append(text_body)
        return True

    monkeypatch.setattr("app.workers.tasks.email_send.queue_email", fake_queue)
    return letters


def _email_code(client: TestClient, outbox: list[str], email: str) -> str:
    response = client.post("/api/auth/email/request-code", json={"email": email, "consent": True})
    assert response.status_code == 200
    words = outbox[-1].replace("\n", " ").split()
    return next(word for word in words if word.isdigit() and len(word) == 6)


def test_email_verify_takes_next_from_the_body(client: TestClient, outbox: list[str]) -> None:
    email = f"runner-{uuid4().hex[:8]}@example.com"
    code = _email_code(client, outbox, email)
    response = client.post("/api/auth/email/verify", json={"email": email, "code": code, "next": PAGE})
    assert response.status_code == 200
    assert response.json()["redirect"] == f"welcome?next={PAGE_ENCODED}"


def test_email_verify_without_or_with_foreign_next(client: TestClient, outbox: list[str]) -> None:
    email = f"runner-{uuid4().hex[:8]}@example.com"
    code = _email_code(client, outbox, email)
    response = client.post(
        "/api/auth/email/verify", json={"email": email, "code": code, "next": "https://evil.com"}
    )
    assert response.status_code == 200
    assert response.json()["redirect"] == "welcome"


# --- Виджет Telegram ------------------------------------------------------------


def _signed_widget_data(telegram_id: int) -> dict[str, str]:
    data = {"id": str(telegram_id), "first_name": "Дмитрий", "username": "runner", "auth_date": str(int(time.time()))}
    check_string = "\n".join(f"{key}={data[key]}" for key in sorted(data))
    secret = hashlib.sha256(WIDGET_BOT_TOKEN.encode()).digest()
    data["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return data


def _start_widget(client: TestClient, **params: str) -> str:
    response = client.get("/api/auth/telegram/start", params=params, follow_redirects=False)
    assert response.status_code == 302
    return_to = parse_qs(urlsplit(response.headers["location"]).query)["return_to"][0]
    return parse_qs(urlsplit(return_to).query)["state"][0]


def test_widget_login_returns_to_the_page(client: TestClient, fake_redis: fakeredis.FakeRedis) -> None:
    state = _start_widget(client, consent="true", next=PAGE)
    stored = json.loads(fake_redis.get(f"auth:tg:state:{state}"))
    assert stored["return_path"] == PAGE

    response = client.post("/api/auth/telegram/widget", json={"data": _signed_widget_data(551000020), "state": state})
    assert response.status_code == 200
    assert response.json()["redirect"] == f"welcome?next={PAGE_ENCODED}"


def test_widget_start_drops_foreign_or_link_mode_next(
    client: TestClient, fake_redis: fakeredis.FakeRedis
) -> None:
    foreign = _start_widget(client, consent="true", next="//evil.com")
    assert "return_path" not in json.loads(fake_redis.get(f"auth:tg:state:{foreign}"))

    # Привязка всегда заканчивается в настройках — возврат ей не нужен.
    _bot_session(client, 551000021)
    linking = _start_widget(client, mode="link", next=PAGE)
    stored = json.loads(fake_redis.get(f"auth:tg:state:{linking}"))
    assert stored["mode"] == "link"
    assert "return_path" not in stored
