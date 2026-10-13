from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.abuse_store import is_telegram_banned
from app.core.admin import is_admin_user

# Единственная реализация — в core/client_ip.py; здесь только реэкспорт для
# роутов, которые импортируют её отсюда. До 13.09.2026 тут жила вторая копия,
# бравшая ПЕРВЫЙ элемент X-Forwarded-For, то есть адрес, который клиент
# написал сам, — подробности в docstring core/client_ip.py.
from app.core.client_ip import get_client_ip as get_client_ip
from app.core.session import delete_session, get_session_user_id
from app.db.session import get_db
from app.models import User

logger = logging.getLogger(__name__)


def get_optional_session_user_id(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> UUID | None:
    signed = request.cookies.get(settings.session_cookie_name)
    if signed is None:
        return None
    user_id = get_session_user_id(settings, signed)
    if user_id is None:
        return None
    return UUID(user_id)


def _forget_orphan_session(request: Request, settings: Settings) -> None:
    """Сессия жива, а профиля нет: его удалили с другого устройства или
    поглотило объединение аккаунтов.

    Индекса «сессии человека» в Redis нет, и обходить все session:* при
    удалении профиля дорого. Поэтому такие сессии гасим здесь, на первом же
    запросе с них: иначе каждый заход продлевал бы мёртвую сессию ещё на 30
    дней (скользящий TTL в get_session_user_id).
    """
    signed = request.cookies.get(settings.session_cookie_name)
    if not signed:
        return
    try:
        delete_session(settings, signed)
    except Exception:  # noqa: BLE001 — не вышло сейчас, выйдет на следующем запросе
        logger.exception("auth: failed to drop session of a missing user")


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    user_id: Annotated[UUID | None, Depends(get_optional_session_user_id)],
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if user is None:
        _forget_orphan_session(request, settings)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    from app.core.abuse_store import remember_user_ip

    if user.telegram_id is not None:
        remember_user_ip(user.telegram_id, get_client_ip(request))
        if is_telegram_banned(user.telegram_id) and not is_admin_user(user, settings):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account temporarily suspended")
    return user


def get_optional_user(
    db: Annotated[Session, Depends(get_db)],
    user_id: Annotated[UUID | None, Depends(get_optional_session_user_id)],
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> User | None:
    if user_id is None:
        return None
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if user is None:
        _forget_orphan_session(request, settings)
    return user


def get_current_admin_user(
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    if not is_admin_user(user, settings):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
