"""Аватарка: EXIF — себе, файл в публичный бакет — чистым (SEC-04).

Оригинал раньше клали «как пришёл», то есть вместе с GPS-меткой снимка (обычно
дом человека), моделью телефона и временем съёмки: файл лежит в публичном
бакете, и прочитать теги мог любой, кто открыл прямую ссылку. Решение
Дмитрия 21.09.2026: теги хранить в users.avatar_exif (приватно, наружу через
API не отдавать), файл класть без метаданных. Проверяем сам маршрут
загрузки, а не только помощник: легко вычистить метаданные в image_processing
и забыть позвать его из роутера.
"""

from __future__ import annotations

import io
from collections.abc import Generator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from PIL.ExifTags import IFD
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.core import media_storage
from app.core.image_processing import has_exif
from app.db.session import get_db
from app.main import app
from app.models import User


def _exif_with_gps() -> Image.Exif:
    exif = Image.Exif()
    exif[0x0110] = "TestPhone"  # Model
    exif.get_ifd(IFD.Exif)[0x9003] = "2026:09:20 09:00:00"  # DateTimeOriginal
    gps = exif.get_ifd(IFD.GPSInfo)
    gps[1], gps[2] = "N", (55.0, 45.0, 0.0)
    gps[3], gps[4] = "E", (37.0, 37.0, 0.0)
    return exif


def _jpeg_with_gps() -> bytes:
    """Снимок 60×80 с моделью телефона и GPS-меткой — как отдаёт телефон."""
    out = io.BytesIO()
    Image.new("RGB", (60, 80), (10, 120, 200)).save(out, format="JPEG", exif=_exif_with_gps())
    return out.getvalue()


def _heic_with_gps() -> bytes | None:
    """HEIC с теми же тегами; None, если libheif собран без энкодера."""
    out = io.BytesIO()
    try:
        Image.new("RGB", (60, 80), (10, 120, 200)).save(out, format="HEIF", exif=_exif_with_gps().tobytes())
    except (OSError, ValueError, KeyError):
        return None
    return out.getvalue()


class _MemoryStorage:
    """Хранилище в памяти: тест смотрит на то, ЧТО улетело в бакет."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        self.objects[key] = data

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def public_url(self, key: str) -> str:
        return f"https://example.test/{key}"


@pytest.fixture
def avatar_client(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[TestClient, _MemoryStorage, User], None, None]:
    user = User(id=uuid4(), display_name="Аватар Тестович")
    db_session.add(user)
    db_session.flush()

    storage = _MemoryStorage()
    monkeypatch.setattr(media_storage, "get_media_storage", lambda: storage)
    monkeypatch.setattr("app.api.routes.avatars.get_media_storage", lambda: storage)

    settings = Settings(app_secret_key="test", s3_bucket="test-bucket", s3_prefix_avatars="avatars")
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield TestClient(app), storage, user
    finally:
        app.dependency_overrides.clear()


def _upload(client: TestClient, raw: bytes, name: str, content_type: str) -> dict[str, Any]:
    response = client.post("/api/users/me/avatar", files={"file": (name, raw, content_type)})
    assert response.status_code == 200, response.text
    return response.json()


def test_uploaded_avatar_loses_exif_but_tags_stay_private(
    avatar_client: tuple[TestClient, _MemoryStorage, User], db_session: Session
) -> None:
    client, storage, user = avatar_client
    raw = _jpeg_with_gps()
    assert has_exif(raw), "исходник теста обязан нести EXIF, иначе проверка ничего не значит"

    payload = _upload(client, raw, "selfie.jpg", "image/jpeg")

    assert storage.objects, "в хранилище ничего не легло"
    for key, data in storage.objects.items():
        assert not has_exif(data), f"в {key} остался EXIF"
    # Оригинал по-прежнему полноразмерный: чистим метаданные, а не картинку.
    assert user.avatar_full_path is not None and user.avatar_full_path.endswith(".jpg")
    with Image.open(io.BytesIO(storage.objects[user.avatar_full_path])) as image:
        assert image.size == (60, 80)

    # Теги — у себя, в приватной колонке.
    db_session.refresh(user)
    assert user.avatar_exif is not None
    assert user.avatar_exif["model"] == "TestPhone"
    assert user.avatar_exif["datetime_original"] == "2026:09:20 09:00:00"
    assert user.avatar_exif["gps"] == {"lat": 55.75, "lon": 37.616667}

    # …и наружу не отдаются: ни ответом загрузки, ни /auth/me.
    assert "avatar_exif" not in payload
    me = client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert "avatar_exif" not in me.json()
    assert me.json()["avatar_full_url"]


def test_delete_avatar_forgets_exif(avatar_client: tuple[TestClient, _MemoryStorage, User], db_session: Session) -> None:
    client, storage, user = avatar_client
    _upload(client, _jpeg_with_gps(), "selfie.jpg", "image/jpeg")
    db_session.refresh(user)
    assert user.avatar_exif

    response = client.delete("/api/users/me/avatar")
    assert response.status_code == 200, response.text
    db_session.refresh(user)
    assert user.avatar_exif is None
    assert not storage.objects


def test_heic_avatar_is_stored_as_clean_jpeg(avatar_client: tuple[TestClient, _MemoryStorage, User], db_session: Session) -> None:
    """HEIC с айфона: в бакет — JPEG под расширением .jpg, теги — в базу."""
    client, storage, user = avatar_client
    raw = _heic_with_gps()
    if raw is None:
        pytest.skip("pillow-heif без энкодера HEIF")

    _upload(client, raw, "IMG_0001.HEIC", "image/heic")

    assert user.avatar_full_path is not None and user.avatar_full_path.endswith(".jpg")
    full = storage.objects[user.avatar_full_path]
    assert not has_exif(full)
    with Image.open(io.BytesIO(full)) as image:
        assert image.format == "JPEG"
        assert image.size == (60, 80)
    db_session.refresh(user)
    assert user.avatar_exif is not None
    assert user.avatar_exif["model"] == "TestPhone"
    assert user.avatar_exif["gps"]["lat"] == 55.75


def test_avatar_without_exif_stores_null(avatar_client: tuple[TestClient, _MemoryStorage, User], db_session: Session) -> None:
    client, _storage, user = avatar_client
    out = io.BytesIO()
    Image.new("RGB", (20, 20), (1, 2, 3)).save(out, format="PNG")

    _upload(client, out.getvalue(), "plain.png", "image/png")

    assert user.avatar_full_path is not None and user.avatar_full_path.endswith(".png")
    db_session.refresh(user)
    assert user.avatar_exif is None
