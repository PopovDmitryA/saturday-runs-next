"""Публичный поиск по сайту и приём журнала поисковых запросов.

Оба адреса открыты анониму. От перебора их закрывает общая защита
(AbuseProtectionMiddleware) отдельным тарифом «search» — см.
classify_route в core/abuse_protection.py.
"""

from __future__ import annotations

import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_optional_session_user_id
from app.config import Settings, get_settings
from app.core.admin import is_admin_user
from app.core.bot_detection import is_bot_user_agent
from app.db.session import get_db
from app.models import User
from app.schemas.search import SiteSearchResponse
from app.services.participant_search_service import MAX_QUERY_LENGTH
from app.services.search_log_service import record_search
from app.services.site_search_service import site_search

router = APIRouter(prefix="/search", tags=["search"])

# Бекон с журналом — десяток полей; больше — не наш клиент.
_MAX_LOG_BODY_BYTES = 4096


@router.get("", response_model=SiteSearchResponse)
def search_site(
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[str, Query(max_length=MAX_QUERY_LENGTH * 2)] = "",
) -> SiteSearchResponse:
    """Локации (от 2 знаков) и люди (от 3 знаков) по одной строке запроса."""
    return SiteSearchResponse.model_validate(site_search(db, q))


async def _raw_body(request: Request) -> bytes:
    """Тело запроса как есть — чтобы сам обработчик остался синхронным.

    Синхронный обработчик FastAPI уводит в пул потоков, и запись в БД не
    блокирует цикл событий; прочитать тело можно только асинхронно.
    """
    return await request.body()


@router.post("/log", status_code=204)
def log_search(
    request: Request,
    raw: Annotated[bytes, Depends(_raw_body)],
    db: Annotated[Session, Depends(get_db)],
    session_user_id: Annotated[UUID | None, Depends(get_optional_session_user_id)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Одна строка журнала на поиск; фронт шлёт её через navigator.sendBeacon.

    Тело разбираем сами, а не через pydantic-модель: sendBeacon со строкой
    отправляет Content-Type text/plain, и FastAPI такое тело как JSON не
    примет. Ответ всегда 204 — бекону ответ всё равно не нужен, а журнал не
    должен ничего сообщать о своих правилах отбора.
    """
    no_content = Response(status_code=204)
    if is_bot_user_agent(request.headers.get("user-agent")):
        return no_content
    if not raw or len(raw) > _MAX_LOG_BODY_BYTES:
        return no_content
    try:
        payload: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return no_content
    if not isinstance(payload, dict):
        return no_content

    if session_user_id is not None:
        # Поиски админа — это его проверки сайта, а не спрос: в «Популярности»
        # его просмотры не пишутся по той же причине.
        viewer = db.get(User, session_user_id)
        if viewer is not None and is_admin_user(viewer, settings):
            return no_content
    record_search(db, payload, is_authed=session_user_id is not None)
    return no_content
