"""Хранилище пользовательских картинок: публичный S3 или локальный диск.

Два потребителя (фото в отзывах на локации и фото в карточках бэклога) кладут
файлы в РАЗНЫЕ папки одного бакета — префикс задаётся вызывающим кодом
(settings.s3_prefix_location_reviews / s3_prefix_backlog).

Почему два бэкенда. На проде бакет публичный: в БД лежит только ключ, браузер
идёт за картинкой прямо в S3, API раздачей не занимается. Локально (dev, CI,
pytest) кред нет и заводить их не нужно — если `s3_bucket` пуст, тот же
интерфейс пишет в `settings.media_dir`, а ссылки ведут на `/api/media/{key}`.
Поэтому весь остальной код не знает, где физически лежит файл, и не содержит
ни одной развилки «а если S3 не настроен».

Ключ всегда начинается с папки потребителя и заканчивается случайным именем
файла (чужой ключ не подобрать перебором id сущности). Между ними — либо
год-месяц, либо осмысленные сегменты: у фото отзывов это slug локации и id
отзыва, чтобы в бакете было видно, к чему относится картинка (просьба
Дмитрия 28.07.2026).
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import Settings, get_settings

CONTENT_TYPE = "image/jpeg"
# Оригиналы аватарок кладутся как есть, без перекодирования, поэтому тип
# определяется расширением ключа, а не считается всегда JPEG.
CONTENT_TYPE_BY_EXTENSION = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}


def content_type_for(key: str) -> str:
    return CONTENT_TYPE_BY_EXTENSION.get(key.rsplit(".", 1)[-1].lower(), CONTENT_TYPE)


# Ключ приходит из БД и подставляется в путь на диске — пускаем только тот
# алфавит, который сами и генерируем (никаких ".." и абсолютных путей).
_KEY_ALPHABET = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_/.")


class MediaStorageError(RuntimeError):
    """Не удалось записать/удалить файл в хранилище."""


def slugify_segment(value: str, *, fallback: str = "unknown") -> str:
    """Кусок ключа из внешних данных: только [a-z0-9-], иначе fallback.

    Ключ объекта = часть публичного URL, поэтому в него не должно попадать
    ничего, кроме заведомо безопасного алфавита (см. is_safe_key).
    """
    cleaned = "".join(char if char.isalnum() and char.isascii() else "-" for char in value.lower())
    cleaned = "-".join(part for part in cleaned.split("-") if part)[:64]
    return cleaned or fallback


def build_key(prefix: str, *, extension: str = "jpg", segments: Sequence[str] = ()) -> str:
    """Ключ объекта: папка потребителя, необязательные осмысленные сегменты,
    случайное имя файла.

    Без segments путь разбивается по году-месяцу — иначе листинг бакета со
    временем становится нечитаемым. С segments (например slug локации и id
    отзыва) структура уже осмысленная, и дата в пути только мешала бы искать
    «все фото этой локации». Случайный хвост в имени остаётся всегда: по нему
    чужой файл не подобрать перебором id.
    """
    parts = [prefix.strip("/")]
    if segments:
        parts.extend(slugify_segment(segment) for segment in segments)
    else:
        parts.append(f"{datetime.now(timezone.utc):%Y/%m}")
    return f"{'/'.join(parts)}/{secrets.token_hex(16)}.{extension}"


def is_safe_key(key: str) -> bool:
    if not key or key.startswith("/") or ".." in key:
        return False
    return set(key) <= _KEY_ALPHABET


class MediaStorage(Protocol):
    def put(self, key: str, data: bytes, content_type: str | None = None) -> None: ...

    def delete(self, key: str) -> None: ...

    def public_url(self, key: str) -> str: ...


class LocalMediaStorage:
    """Файлы на диске, раздача через /api/media/{key} — только для dev/тестов."""

    def __init__(self, root: str) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        return self._root / key

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        # Тип на диске не хранится — его отдаёт роут /api/media по расширению.
        del content_type
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as exc:
            raise MediaStorageError(str(exc)) from exc

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError as exc:
            raise MediaStorageError(str(exc)) from exc

    def public_url(self, key: str) -> str:
        return f"/api/media/{key}"

    def local_path(self, key: str) -> Path:
        return self._path(key)


class S3MediaStorage:
    """Публичный S3-совместимый бакет (прод, Timeweb).

    Объекты кладутся с ACL public-read: раздачей занимается сам бакет, а не
    наш API — это и дешевле по трафику, и снимает нагрузку с одного контейнера.
    """

    def __init__(self, settings: Settings) -> None:
        # Локальный импорт: без S3-кред boto3 вообще не нужен.
        import boto3  # type: ignore[import-untyped]

        self._bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            region_name=settings.s3_region or None,
            aws_access_key_id=settings.s3_access_key_id or None,
            aws_secret_access_key=settings.s3_secret_access_key or None,
        )
        base = settings.s3_public_base_url.rstrip("/")
        if not base:
            endpoint = (settings.s3_endpoint_url or "").rstrip("/")
            base = f"{endpoint}/{self._bucket}" if endpoint else ""
        self._public_base = base

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type or content_type_for(key),
                ACL="public-read",
                # Имя файла случайное и никогда не переиспользуется — можно
                # кэшировать в браузере и на CDN сколь угодно долго.
                CacheControl="public, max-age=31536000, immutable",
            )
        except Exception as exc:  # noqa: BLE001 — botocore-ошибки не импортируем ради типа
            raise MediaStorageError(str(exc)) from exc

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except Exception as exc:  # noqa: BLE001
            raise MediaStorageError(str(exc)) from exc

    def public_url(self, key: str) -> str:
        return f"{self._public_base}/{key}" if self._public_base else key


@lru_cache
def get_media_storage() -> MediaStorage:
    settings = get_settings()
    if settings.s3_bucket:
        return S3MediaStorage(settings)
    return LocalMediaStorage(settings.media_dir)


def reset_media_storage_cache() -> None:
    """Сбросить выбранный бэкенд (тесты подменяют настройки на лету)."""
    get_media_storage.cache_clear()
