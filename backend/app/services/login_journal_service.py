"""Журнал входов/выходов пользователей.

Пишется на каждый логин (oauth, magic link, merge) и на каждый разлогин.
Читается админкой: по журналу видно, слетела ли сессия сама — логин без
предшествующего logout с того же устройства означает, что авторизация
оборвалась не по воле пользователя.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.security import verify_signed_session_id
from app.models import LoginEvent

EVENT_LOGIN = "login"
EVENT_LOGOUT = "logout"

_REF_LEN = 16


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:_REF_LEN]


def session_ref_from_signed(signed_session: str | None, secret_key: str) -> str:
    """Стабильная метка сессии, по которой login матчится с logout.

    Сам session_id не храним — он равносилен паролю. Подпись отбрасываем:
    у одной сессии подпись стабильна, но хэшируем именно id, чтобы logout
    сматчился с login независимо от формата куки.
    """
    if not signed_session:
        return ""
    session_id = verify_signed_session_id(signed_session, secret_key)
    if session_id is None:
        return ""
    return _short_hash(session_id)


def _device_ref(user_agent: str, ip: str) -> str:
    if not user_agent and not ip:
        return ""
    return _short_hash(f"{user_agent}|{ip}")


def record_login_event(
    db: Session,
    *,
    user_id: UUID,
    event_type: str,
    provider: str = "",
    session_ref: str = "",
    request: Request | None = None,
) -> None:
    """Записать событие журнала.

    Журнал — диагностика, а не бизнес-логика: сбой записи не должен ломать
    сам вход, поэтому вызывающий код не обязан ждать commit.
    """
    ip = ""
    user_agent = ""
    if request is not None:
        # Локальный импорт: deps тянет настройки, а сервис зовётся и из тестов.
        from app.api.deps import get_client_ip

        ip = get_client_ip(request)[:64]
        user_agent = (request.headers.get("User-Agent") or "")[:256]

    db.add(
        LoginEvent(
            user_id=user_id,
            event_type=event_type,
            provider=provider[:32],
            session_ref=session_ref,
            ip=ip,
            user_agent=user_agent,
            device_ref=_device_ref(user_agent, ip),
        )
    )


def list_login_events(db: Session, user_id: UUID, *, limit: int = 100) -> list[LoginEvent]:
    return (
        db.query(LoginEvent)
        .filter(LoginEvent.user_id == user_id)
        .order_by(LoginEvent.ts.desc())
        .limit(limit)
        .all()
    )


def summarize_login_events(events: list[LoginEvent]) -> dict[str, object]:
    """Свести журнал в ответ на вопрос «сессии рвутся сами?».

    events приходит от новых к старым. Логин считается вынужденным, если
    с того же устройства уже был логин, а разлогина между ними не было:
    значит прошлая сессия исчезла сама.
    """
    ordered = sorted(events, key=lambda e: e.ts)
    logins = [e for e in ordered if e.event_type == EVENT_LOGIN]
    logouts = [e for e in ordered if e.event_type == EVENT_LOGOUT]

    seen_devices: set[str] = set()
    logged_out_devices: set[str] = set()
    unexpected: list[dict[str, object]] = []

    for event in ordered:
        device = event.device_ref
        if event.event_type == EVENT_LOGOUT:
            logged_out_devices.add(device)
            continue
        if device and device in seen_devices and device not in logged_out_devices:
            unexpected.append(
                {
                    "ts": event.ts,
                    "provider": event.provider,
                    "ip": event.ip,
                    "user_agent": event.user_agent,
                }
            )
        seen_devices.add(device)
        logged_out_devices.discard(device)

    return {
        "logins": len(logins),
        "logouts": len(logouts),
        "devices": len({e.device_ref for e in ordered if e.device_ref}),
        # Логины, которым не предшествовал разлогин с того же устройства.
        "unexpected_relogins": len(unexpected),
        "unexpected_samples": unexpected[-5:],
    }


def purge_old_login_events(db: Session, *, retention_days: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted = db.query(LoginEvent).filter(LoginEvent.ts < cutoff).delete(synchronize_session=False)
    db.commit()
    return int(deleted)
