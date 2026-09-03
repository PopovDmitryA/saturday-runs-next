"""Перекодировка снимка для постера: HEIC → JPEG, без сохранения."""

from __future__ import annotations

import io
from collections.abc import Generator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.models import User
from tests.test_organizer_api import ADMIN_TELEGRAM_ID, _login


# Те же фикстуры, что у organizer_api: pytest регистрирует фикстуру по имени
# в пространстве модуля, поэтому импорт под псевдонимом не работает.
@pytest.fixture
def auth_settings() -> Settings:
    return Settings(
        app_secret_key="test-secret-key",
        app_debug=True,
        telegram_bot_internal_secret="bot-secret",
        telegram_bot_username="TestBot",
        admin_telegram_id=ADMIN_TELEGRAM_ID,
        database_url=get_settings().database_url,
        redis_url="redis://localhost:6379/0",
    )


@pytest.fixture
def client(db_session: Session, fake_redis, auth_settings: Settings) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: auth_settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _heic_bytes() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), (10, 120, 200)).save(out, format="HEIF")
    return out.getvalue()


def _login_any(client: TestClient, db_session: Session) -> None:
    user = User(telegram_id=int(uuid4().int % 10_000_000_000), telegram_username=f"u{uuid4().hex[:6]}")
    db_session.add(user)
    db_session.commit()
    _login(client, user.telegram_id or 0, user.telegram_username or "u")


def test_convert_requires_login(client: TestClient) -> None:
    response = client.post("/api/media/convert-image", files={"file": ("a.heic", _heic_bytes(), "image/heic")})
    assert response.status_code == 401


def test_convert_returns_jpeg(client: TestClient, db_session: Session) -> None:
    _login_any(client, db_session)
    response = client.post("/api/media/convert-image", files={"file": ("a.heic", _heic_bytes(), "image/heic")})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(response.content)).format == "JPEG"


def test_convert_rejects_garbage(client: TestClient, db_session: Session) -> None:
    _login_any(client, db_session)
    response = client.post("/api/media/convert-image", files={"file": ("a.heic", b"not an image", "image/heic")})
    assert response.status_code == 400
