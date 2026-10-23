"""Шапка публичного профиля не отдаёт гостю личные данные.

До 23.10.2026 /api/users/{serial}/profile/dashboard отвечал схемой админки:
любой без входа получал почту и способы входа, Telegram ID и ник, штрихкоды
привязок — а номера профилей идут подряд, так что выкачивались все.
"""

from __future__ import annotations

from collections.abc import Generator
from uuid import uuid4

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.models import AuthIdentity, AuthProvider, Participant, Platform, PlatformLink, User
from app.services.admin_users_service import _public_link_brief

EMAIL = "leak.check@example.com"
BARCODE = "A123456789"


@pytest.fixture
def client(db_session: Session, fake_redis: fakeredis.FakeRedis) -> Generator[TestClient, None, None]:
    settings = Settings(
        app_secret_key="test-secret-key",
        app_debug=True,
        database_url=get_settings().database_url,
        redis_url="redis://localhost:6379/0",
    )

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_guest_gets_no_contacts_logins_or_barcodes(client: TestClient, db_session: Session) -> None:
    user = User(
        telegram_id=int(uuid4().int % 10_000_000_000),
        telegram_username=f"leak{uuid4().int % 1_000_000}",
        display_name="Иван Петров",
        consent_accepted=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(
        AuthIdentity(
            user_id=user.id,
            provider=AuthProvider.yandex,
            external_id=f"yandex-{uuid4().hex[:8]}",
            display_name="Иван",
            email=EMAIL,
            profile_json={},
        )
    )
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    external_id = str(uuid4().int % 1_000_000_000)
    participant = Participant(
        platform_id=platform.id,
        external_user_id=external_id,
        display_name="ПЕТРОВ Иван",
        barcode_id=BARCODE,
        profile_url=f"https://5verst.ru/userstats/{external_id}/",
    )
    db_session.add(participant)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            external_user_id=external_id,
            external_url=f"https://5verst.ru/userstats/{external_id}/",
        )
    )
    db_session.commit()

    response = client.get(f"/api/users/{user.serial_id}/profile/dashboard")
    assert response.status_code == 200
    body = response.text
    payload = response.json()

    assert set(payload["user"]) == {"display_name", "avatar_url", "avatar_full_url"}
    for leaked in (EMAIL, "@", BARCODE, str(user.id), str(user.telegram_id), user.telegram_username, "ПЕТРОВ"):
        assert leaked not in body, leaked
    for key in ("auth_logins", "telegram", "barcode", "sync_status", "news_subscribed"):
        assert key not in body, key
    # Бейдж системы по-прежнему ведёт в свой профиль на ней (решение 26.09.2026).
    assert payload["platform_links"] == [
        {
            "platform_code": "five_verst",
            "external_user_id": "",
            "external_url": f"https://5verst.ru/userstats/{external_id}/",
            "run_count": 0,
            "volunteer_count": 0,
        }
    ]


def _link(platform_code: str, external_user_id: str, external_url: str) -> dict[str, object]:
    return {
        "platform_code": platform_code,
        "external_user_id": external_user_id,
        "external_url": external_url,
        "display_name": "ПЕТРОВ Иван",
        "sync_status": "ok",
        "run_count": 3,
        "volunteer_count": 1,
        "barcode_id": BARCODE,
    }


def test_runpark_link_keeps_only_account_guid() -> None:
    guid = "0f8fad5b-d9cb-469f-a165-70867728950e"
    assert _public_link_brief(_link("runpark", guid, BARCODE)) == {
        "platform_code": "runpark",
        "external_user_id": guid,
        "external_url": "",
        "run_count": 3,
        "volunteer_count": 1,
    }
    # «barcode:A…» — наш ключ строки протокола без аккаунта: ссылки нет, штрихкод не уходит.
    brief = _public_link_brief(_link("runpark", f"barcode:{BARCODE}", BARCODE))
    assert brief["external_user_id"] == "" and brief["external_url"] == ""


def test_link_without_http_address_is_dropped() -> None:
    brief = _public_link_brief(_link("s95", "790103773", BARCODE))
    assert brief["external_url"] == "" and brief["external_user_id"] == ""
