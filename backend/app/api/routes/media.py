"""Раздача картинок в режиме локального хранилища (dev, тесты).

На проде бакет публичный: браузер идёт за картинкой прямо в S3, и этот роут
не участвует вообще — он отдаёт 404, потому что LocalMediaStorage там не
выбран. Держим его именно ради того, чтобы фича целиком (загрузка → показ)
проверялась локально без единого облачного ключа.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, Response

from app.api.deps import get_current_user
from app.core.image_processing import MAX_UPLOAD_BYTES, ImageProcessingError, process_photo
from app.core.media_storage import LocalMediaStorage, content_type_for, get_media_storage, is_safe_key
from app.models import User

router = APIRouter(tags=["media"])


@router.post("/media/convert-image")
async def convert_image(
    file: UploadFile,
    _user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Перекодировать снимок в JPEG, ничего не сохраняя.

    Нужен постеру «Поделиться»: фон-фото он рисует в браузере, а HEIC с
    айфона браузеры (кроме Safari) не декодируют — кнопка «Фото» молча ничего
    не делала (03.09.2026). Сервер читает HEIC через pillow-heif и отдаёт JPEG
    не длиннее 2560 px; файл на диск не ложится.
    """
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Файл больше 15 МБ — выберите изображение поменьше",
        )
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой файл")
    try:
        data, _w, _h = process_photo(raw)
    except ImageProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/media/{key:path}")
def get_media(key: str) -> FileResponse:
    storage = get_media_storage()
    if not isinstance(storage, LocalMediaStorage):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл не найден")
    # Ключ приходит из URL — валидируем алфавитом, а не санитизацией пути.
    if not is_safe_key(key):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл не найден")
    path = storage.local_path(key)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл не найден")
    return FileResponse(
        path,
        media_type=content_type_for(key),
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )
