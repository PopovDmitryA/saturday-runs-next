"""Фото в отзывах и карточках бэклога: обработка картинки, лимиты, хранилище.

Тесты идут через LocalMediaStorage (S3-кред в dev/CI нет и не должно быть):
проверяем ровно ту же цепочку «обработать → положить в хранилище → строка в
БД», просто бэкенд хранилища локальный.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image
from sqlalchemy.orm import Session

from app.core import media_storage
from app.core.image_processing import MAX_SIDE_PX, ImageProcessingError, process_photo
from app.core.media_storage import LocalMediaStorage, build_key, is_safe_key, slugify_segment
from app.models import (
    BacklogCardType,
    Event,
    Location,
    LocationRating,
    Participant,
    Platform,
    RunResult,
    User,
)
from app.services.backlog_service import create_card
from app.services.photo_service import (
    MAX_BACKLOG_PHOTOS,
    PhotoError,
    add_backlog_photo,
    add_rating_photo,
    list_backlog_photos,
)


def _png_bytes(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_process_photo_downscales_to_max_side() -> None:
    data, width, height = process_photo(_png_bytes(4032, 3024))
    assert max(width, height) == MAX_SIDE_PX
    # Пропорции сохранены (4:3 с точностью до округления).
    assert abs(width / height - 4032 / 3024) < 0.01
    assert Image.open(io.BytesIO(data)).format == "JPEG"


def test_process_photo_keeps_small_image_size() -> None:
    """Маленькую картинку не растягиваем — апскейл только портит кадр."""
    _data, width, height = process_photo(_png_bytes(800, 600))
    assert (width, height) == (800, 600)


def test_process_photo_shrinks_bytes() -> None:
    raw = _png_bytes(4000, 3000)
    data, _w, _h = process_photo(raw)
    assert len(data) < len(raw)


def test_process_photo_rejects_garbage() -> None:
    with pytest.raises(ImageProcessingError):
        process_photo(b"not an image at all")


def test_local_storage_roundtrip(tmp_path: Path) -> None:
    storage = LocalMediaStorage(str(tmp_path))
    key = build_key("location-reviews")
    storage.put(key, b"payload")
    assert storage.local_path(key).read_bytes() == b"payload"
    assert storage.public_url(key) == f"/api/media/{key}"
    storage.delete(key)
    assert not storage.local_path(key).exists()
    # Повторное удаление не должно падать — фото могли снести дважды.
    storage.delete(key)


def test_build_key_uses_prefix_folder() -> None:
    """Два потребителя раскладываются по разным папкам одного бакета."""
    reviews = build_key("location-reviews")
    backlog = build_key("backlog")
    assert reviews.startswith("location-reviews/")
    assert backlog.startswith("backlog/")
    assert reviews.endswith(".jpg")
    # Имя случайное: два подряд сгенерированных ключа не совпадают.
    assert build_key("backlog") != backlog


@pytest.mark.parametrize(
    "key",
    ["../../etc/passwd", "/etc/passwd", "backlog/../../secret.jpg", "", "ключ.jpg"],
)
def test_is_safe_key_rejects_traversal(key: str) -> None:
    assert not is_safe_key(key)


def test_is_safe_key_accepts_generated_keys() -> None:
    assert is_safe_key(build_key("location-reviews"))


def test_storage_backend_follows_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Пустой s3_bucket = локальный диск; заполненный — S3 (кред здесь нет,
    поэтому проверяем только выбор класса, без сетевых вызовов)."""
    settings = media_storage.get_settings()
    monkeypatch.setattr(settings, "s3_bucket", "", raising=False)
    monkeypatch.setattr(settings, "media_dir", str(tmp_path), raising=False)
    media_storage.reset_media_storage_cache()
    assert isinstance(media_storage.get_media_storage(), LocalMediaStorage)
    media_storage.reset_media_storage_cache()


def _seed_card(db: Session) -> UUID:
    user = User(display_name="Фотограф")
    db.add(user)
    db.commit()
    card = create_card(
        db,
        author_id=user.id,
        type_=BacklogCardType.feature,
        category="other",
        title="Карточка с фото",
        description="Описание",
        is_anonymous=False,
    )
    return card.id


def _seed_rating(db: Session) -> LocationRating:
    """Минимальная оценка со своей локацией — нужен только slug в ключе."""
    user = User(display_name="Автор отзыва")
    platform = Platform(code="test_platform", name="Тест", base_url="https://example.test")
    db.add_all([user, platform])
    db.flush()
    location = Location(platform_id=platform.id, external_key="testovo", name="Тестово")
    db.add(location)
    db.flush()
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key="testovo-1",
        event_date=date(2026, 7, 25),
    )
    participant = Participant(platform_id=platform.id, external_user_id="p-1", display_name="Бегун")
    db.add_all([event, participant])
    db.flush()
    run = RunResult(
        event_id=event.id,
        participant_id=participant.id,
        external_result_key="testovo-1-p1",
        finish_time_sec=1500,
    )
    db.add(run)
    db.flush()
    rating = LocationRating(
        user_id=user.id,
        run_result_id=run.id,
        participation_type="run",
        location_id=location.id,
        location_key=f"location:{location.id}",
        event_date=event.event_date,
        platform_code=platform.code,
        score_overall=5,
    )
    db.add(rating)
    db.flush()
    return rating


def test_backlog_photo_limit(db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Четвёртое фото на карточку не принимается — лимит 3."""
    settings = media_storage.get_settings()
    monkeypatch.setattr(settings, "s3_bucket", "", raising=False)
    monkeypatch.setattr(settings, "media_dir", str(tmp_path), raising=False)
    media_storage.reset_media_storage_cache()

    card_id = _seed_card(db_session)
    raw = _png_bytes(1200, 900)
    for _ in range(MAX_BACKLOG_PHOTOS):
        add_backlog_photo(db_session, card_id, raw)
    with pytest.raises(PhotoError):
        add_backlog_photo(db_session, card_id, raw)

    photos = list_backlog_photos(db_session, [card_id])[card_id]
    assert len(photos) == MAX_BACKLOG_PHOTOS
    # Файлы реально лежат в папке потребителя.
    assert all(photo.url.startswith("/api/media/backlog/") for photo in photos)

    media_storage.reset_media_storage_cache()


def test_build_key_with_segments_is_readable_and_safe() -> None:
    """Ключ фото отзыва: папка → slug локации → id отзыва → случайное имя."""
    key = build_key(
        "location-reviews",
        segments=("butovo", "a1b2c3d4-5555-6666-7777-888899990000"),
    )
    assert key.startswith("location-reviews/butovo/a1b2c3d4-5555-6666-7777-888899990000/")
    assert key.endswith(".jpg")
    assert is_safe_key(key)


def test_slugify_segment_strips_unsafe_characters() -> None:
    # Кириллическое имя локации не должно уехать в ключ как есть.
    assert slugify_segment("Кузьминки") == "unknown"
    assert slugify_segment("Butovo Park!") == "butovo-park"
    assert slugify_segment("../../etc") == "etc"
    assert is_safe_key(f"location-reviews/{slugify_segment('../../etc')}/x.jpg")


def test_rating_photo_key_has_no_user_id(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ключ фото отзыва не содержит id участника: он же публичный URL, и по
    общему сегменту можно было бы связать все отзывы одного человека."""
    settings = media_storage.get_settings()
    monkeypatch.setattr(settings, "s3_bucket", "", raising=False)
    monkeypatch.setattr(settings, "media_dir", str(tmp_path), raising=False)
    media_storage.reset_media_storage_cache()

    rating = _seed_rating(db_session)
    photo = add_rating_photo(db_session, rating, _png_bytes(1200, 900))
    db_session.flush()

    assert photo.storage_key.startswith(f"location-reviews/testovo/{rating.id}/")
    assert str(rating.user_id) not in photo.storage_key

    media_storage.reset_media_storage_cache()
