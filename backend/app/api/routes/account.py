"""Профиль целиком: удаление самим человеком (настройки → «Удаление профиля»).

Что уходит и что остаётся — в докстринге services/account_deletion_service.py.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.core.admin import is_admin_user
from app.core.session import delete_session
from app.db.session import get_db
from app.models import User
from app.schemas.account import (
    DELETION_ATTEMPT_PATTERN,
    AccountDeleteRequest,
    AccountDeletionPreviewResponse,
    AccountDeletionStatusResponse,
)
from app.schemas.auth import MessageResponse
from app.services.account_deletion_service import (
    AccountDeletionError,
    confirmation_matches,
    delete_account,
    deletion_attempt_succeeded,
    deletion_preview,
    finalize_account_deletion,
    remember_deletion_attempt,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/account", tags=["account"])

ADMIN_DELETE_FORBIDDEN = "Профиль администратора удалить нельзя"
CONFIRMATION_REQUIRED = "Чтобы удалить профиль, введите слово «удалить»"


@router.get("/deletion-preview", response_model=AccountDeletionPreviewResponse)
def get_deletion_preview(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> AccountDeletionPreviewResponse:
    return deletion_preview(db, user)


@router.post("/delete", response_model=MessageResponse)
def delete_my_account(
    payload: AccountDeleteRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MessageResponse:
    # Админ — это способ входа из настроек сервера, а не строка в базе: после
    # удаления он вошёл бы снова тем же админом, но уже без привязок и своих
    # ответов в бэклоге. Такое делается руками, не кнопкой.
    if is_admin_user(user, settings):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ADMIN_DELETE_FORBIDDEN)
    if not confirmation_matches(payload.confirmation):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=CONFIRMATION_REQUIRED)

    try:
        receipt = delete_account(db, user)
        db.commit()
    except AccountDeletionError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception:
        db.rollback()
        raise

    if payload.attempt_id:
        try:
            remember_deletion_attempt(payload.attempt_id)
        except Exception:  # noqa: BLE001 — без отметки браузер лишь не узнает исход при обрыве
            logger.exception("account deletion: failed to remember attempt of user %s", receipt.user_id)

    # Сессию этого устройства гасим сразу; сессии других устройств сотрёт
    # get_current_user на их следующем запросе (профиля уже нет).
    session_value = request.cookies.get(settings.session_cookie_name)
    if session_value:
        try:
            delete_session(settings, session_value)
        except Exception:  # noqa: BLE001 — профиль уже удалён, сессия без него никуда не пустит
            logger.exception("account deletion: failed to drop session of user %s", receipt.user_id)
    # Те же флаги, что при выдаче (routes/auth.py, _set_session_cookie): иначе
    # браузер не сочтёт куку той же и не сотрёт. sr_known снимаем тоже — по
    # ней экран входа считает согласие данным, а согласия больше нет ни в
    # одном профиле.
    for name, httponly in ((settings.session_cookie_name, True), (settings.known_device_cookie_name, False)):
        response.delete_cookie(
            key=name,
            path="/",
            secure=not settings.app_debug,
            httponly=httponly,
            samesite="lax",
        )
    # Файлы, Redis и заметка админу — фоном, когда ответ уже ушёл: там сеть
    # (бакет, прокси Telegram с таймаутом 30 с), а клиент ждёт те же 30 с.
    # Зависни она в ответе — человек увидел бы «Сервер не ответил» после уже
    # состоявшегося удаления, а куки остались бы в браузере.
    background_tasks.add_task(finalize_account_deletion, settings, receipt)
    return MessageResponse(message="deleted")


@router.get("/deletion-status", response_model=AccountDeletionStatusResponse)
def get_deletion_status(
    attempt: Annotated[str, Query(pattern=DELETION_ATTEMPT_PATTERN)],
) -> AccountDeletionStatusResponse:
    """Удалила ли профиль попытка с этим id — для браузера, до которого не
    дошёл ответ на POST /account/delete. Без входа: сессии к этому моменту
    уже нет, а id — случайная строка, которую знает только этот браузер."""
    return AccountDeletionStatusResponse(deleted=deletion_attempt_succeeded(attempt))
