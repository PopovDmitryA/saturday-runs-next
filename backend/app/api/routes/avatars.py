"""Аватарки пользователей: загрузка, отдача, удаление.

Хранятся в общем media-хранилище (публичный S3 на проде, локальный диск в
dev — см. core/media_storage.py) в отдельной папке `s3_prefix_avatars`, рядом
с папками фото отзывов и бэклога.

С каждой загрузки сохраняется ДВА объекта:
- превью — квадрат 256×256 JPEG (обычно 10–30 КБ): это то, что рисуется в
  интерфейсе, где аватарок на экране бывает много;
- оригинал — байт-в-байт как прислал пользователь, без перекодирования
  (решение Дмитрия 28.07.2026: «не будем их сжимать, а по клику раскрывать»).
  Он открывается по клику на аватарку.

В БД лежат только ключи (users.avatar_path / avatar_full_path). При замене и
удалении старые объекты стираются из хранилища, чтобы не копить сирот.

Роут GET /avatars/{filename} оставлен для аватарок, загруженных ДО переезда на
S3: тогда в avatar_path лежало имя файла в settings.avatars_dir.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.core.admin import user_response
from app.core.media_storage import MediaStorageError, build_key, content_type_for, get_media_storage
from app.db.session import get_db
from app.models import User
from app.schemas.auth import UserResponse

router = APIRouter(tags=["avatars"])

# Больше — почти наверняка не фотография для аватарки, а ошибка.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
AVATAR_SIZE = 256
AVATAR_JPEG_QUALITY = 85
_FILENAME_RE = re.compile(r"^[0-9a-f-]{36}-[0-9a-f]{16}\.jpg$")
# Расширение оригинала берём по формату, который распознал Pillow, а не по
# имени файла от клиента — так в ключ не попадёт ничего произвольного.
_EXTENSION_BY_FORMAT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}


def _avatar_file(settings: Settings, filename: str) -> Path:
    return Path(settings.avatars_dir) / filename


def _delete_stored(key: str | None, settings: Settings) -> None:
    """Снять старую аватарку: ключ хранилища или файл на диске (старый формат)."""
    if not key:
        return
    try:
        if "/" in key:
            get_media_storage().delete(key)
        else:
            _avatar_file(settings, key).unlink(missing_ok=True)
    except (MediaStorageError, OSError):
        # Недоступное хранилище не должно ронять запрос: файл-сирота хуже
        # 500-ки, но заметно менее вреден.
        pass


def _read_image(raw: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не удалось прочитать изображение — поддерживаются JPEG, PNG и WebP",
        ) from exc
    return image


def _make_preview(image: Image.Image) -> bytes:
    """Квадратное превью 256×256 JPEG для показа в интерфейсе."""
    # Фото с телефона часто «лежит на боку» из-за EXIF-ориентации.
    image = ImageOps.exif_transpose(image) or image
    if image.mode != "RGB":
        # PNG с прозрачностью кладём на белый фон, а не на чёрный по умолчанию.
        if image.mode in ("RGBA", "LA", "P"):
            converted = image.convert("RGBA")
            background = Image.new("RGB", converted.size, (255, 255, 255))
            background.paste(converted, mask=converted.split()[-1])
            image = background
        else:
            image = image.convert("RGB")
    image = ImageOps.fit(image, (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=AVATAR_JPEG_QUALITY, optimize=True)
    return out.getvalue()


@router.post("/users/me/avatar", response_model=UserResponse)
async def upload_avatar(
    file: UploadFile,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserResponse:
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Файл больше 10 МБ — выберите изображение поменьше",
        )
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Пустой файл")

    image = _read_image(raw)
    extension = _EXTENSION_BY_FORMAT.get(image.format or "", "jpg")
    preview = _make_preview(image)

    prefix = settings.s3_prefix_avatars
    preview_key = build_key(prefix)
    full_key = build_key(prefix, extension=extension)
    storage = get_media_storage()
    try:
        storage.put(preview_key, preview)
        # Оригинал кладём как пришёл — без ресайза и перекодирования.
        storage.put(full_key, raw, content_type_for(full_key))
    except MediaStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Хранилище картинок недоступно, попробуйте позже",
        ) from exc

    old_preview, old_full = user.avatar_path, user.avatar_full_path
    user.avatar_path = preview_key
    user.avatar_full_path = full_key
    db.commit()
    # Старые файлы стираем ПОСЛЕ коммита: если коммит упал, аватарка не потеряна.
    if old_preview != preview_key:
        _delete_stored(old_preview, settings)
    if old_full != full_key:
        _delete_stored(old_full, settings)
    # Ответ собираем канонически (как /auth/me): model_validate на ORM-модели
    # падает на вложенных auth_identities и не проставляет is_admin.
    return user_response(user, settings, db=db)


@router.delete("/users/me/avatar", response_model=UserResponse)
def delete_avatar(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserResponse:
    old_preview, old_full = user.avatar_path, user.avatar_full_path
    user.avatar_path = None
    user.avatar_full_path = None
    db.commit()
    _delete_stored(old_preview, settings)
    _delete_stored(old_full, settings)
    return user_response(user, settings, db=db)


@router.get("/avatars/{filename}")
def get_avatar(
    filename: str,
    settings: Annotated[Settings, Depends(get_settings)],
) -> FileResponse:
    """Аватарки, загруженные до переезда на S3 (файл на диске)."""
    # Жёсткая валидация имени вместо санитизации пути: никакие "../" не пройдут.
    if not _FILENAME_RE.fullmatch(filename):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл не найден")
    path = _avatar_file(settings, filename)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл не найден")
    # Имя файла меняется при каждой замене — можно кэшировать надолго.
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )
