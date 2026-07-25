"""Аватарки пользователей: загрузка со сжатием, отдача, удаление.

Любая загруженная картинка приводится к маленькому квадратному JPEG
(256×256, качество 85 — обычно 10–30 КБ), поэтому в хранилище никогда не
попадают «сырые» многомегабайтные файлы. При замене или удалении старый файл
стирается с диска совсем — на сервере не остаётся осиротевших картинок.

Файлы лежат в settings.avatars_dir (том ./data:/data), в БД — только имя
"{user_id}-{token}.jpg". Случайный токен в имени ломает браузерный кэш при
замене и не даёт подобрать чужой адрес перебором user_id.
"""

from __future__ import annotations

import io
import re
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.core.admin import user_response
from app.db.session import get_db
from app.models import User
from app.schemas.auth import UserResponse

router = APIRouter(tags=["avatars"])

# Больше — почти наверняка не фотография для аватарки, а ошибка.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
AVATAR_SIZE = 256
AVATAR_JPEG_QUALITY = 85
_FILENAME_RE = re.compile(r"^[0-9a-f-]{36}-[0-9a-f]{16}\.jpg$")


def _avatar_file(settings: Settings, filename: str) -> Path:
    return Path(settings.avatars_dir) / filename


def _delete_avatar_file(settings: Settings, filename: str | None) -> None:
    if not filename:
        return
    try:
        _avatar_file(settings, filename).unlink(missing_ok=True)
    except OSError:
        # Недоступный диск не должен ронять запрос: файл-сирота хуже 500-ки,
        # но заметно менее вреден.
        pass


def _process_image(raw: bytes) -> bytes:
    """Любой поддерживаемый формат → квадратный JPEG 256×256."""
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не удалось прочитать изображение — поддерживаются JPEG, PNG и WebP",
        ) from exc
    # Фото с телефона часто «лежит на боку» из-за EXIF-ориентации.
    image = ImageOps.exif_transpose(image) or image
    if image.mode != "RGB":
        # PNG с прозрачностью кладём на белый фон, а не на чёрный по умолчанию.
        if image.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image.convert("RGBA"), mask=image.convert("RGBA").split()[-1])
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

    processed = _process_image(raw)

    filename = f"{user.id}-{secrets.token_hex(8)}.jpg"
    directory = Path(settings.avatars_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_bytes(processed)

    old_filename = user.avatar_path
    user.avatar_path = filename
    db.commit()
    # Старый файл стираем ПОСЛЕ коммита: если коммит упал, аватарка не потеряна.
    if old_filename and old_filename != filename:
        _delete_avatar_file(settings, old_filename)
    # Ответ собираем канонически (как /auth/me): model_validate на ORM-модели
    # падает на вложенных auth_identities и не проставляет is_admin.
    return user_response(user, settings)


@router.delete("/users/me/avatar", response_model=UserResponse)
def delete_avatar(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserResponse:
    old_filename = user.avatar_path
    user.avatar_path = None
    db.commit()
    _delete_avatar_file(settings, old_filename)
    return user_response(user, settings)


@router.get("/avatars/{filename}")
def get_avatar(
    filename: str,
    settings: Annotated[Settings, Depends(get_settings)],
) -> FileResponse:
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
