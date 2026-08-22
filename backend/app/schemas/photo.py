from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class PhotoResponse(BaseModel):
    """Загруженное фото: готовая ссылка (публичный S3 или /api/media в dev).

    Общая схема для всех потребителей (отзывы на локации, карточки бэклога) —
    ссылку собирает бэкенд, фронт нигде не склеивает адрес бакета сам.
    """

    id: UUID
    url: str
    width: int
    height: int
