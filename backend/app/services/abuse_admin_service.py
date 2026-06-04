from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import Settings
from app.core.abuse_store import (
    IpBlockRecord,
    TelegramBanRecord,
    clear_ip_abuse_score,
    get_ip_block_record,
    get_telegram_ban_record,
    get_user_last_ip,
    is_valid_ip,
    list_ip_blocks,
    list_telegram_bans,
    remove_ip_block,
    remove_telegram_ban,
    set_ip_block,
    set_telegram_ban,
)
from app.core.admin import is_admin_telegram_id
from app.models import User


class AbuseAdminError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class ResolvedBanTarget:
    ips: list[str]
    telegram_ids: list[int]
    label: str


def _admin_label(user: User) -> str:
    if user.telegram_username:
        return f"@{user.telegram_username.lstrip('@')}"
    return str(user.telegram_id)


def _resolve_user(db: Session, raw: str) -> User | None:
    value = raw.strip()
    if not value:
        return None
    if value.isdigit():
        return db.query(User).filter(User.telegram_id == int(value)).one_or_none()
    username = value.lstrip("@")
    return db.query(User).filter(User.telegram_username.ilike(username)).one_or_none()


def resolve_ban_target(db: Session, target: str) -> ResolvedBanTarget:
    value = target.strip()
    if not value:
        raise AbuseAdminError("Укажите IP, Telegram ID или @username")

    if is_valid_ip(value):
        return ResolvedBanTarget(ips=[value], telegram_ids=[], label=value)

    if value.isdigit():
        telegram_id = int(value)
        user = db.query(User).filter(User.telegram_id == telegram_id).one_or_none()
        label = f"telegram:{telegram_id}"
        if user and user.telegram_username:
            label = f"@{user.telegram_username.lstrip('@')}"
        return ResolvedBanTarget(ips=[], telegram_ids=[telegram_id], label=label)

    if value.startswith("@") or re.fullmatch(r"[A-Za-z0-9_]{3,32}", value):
        user = _resolve_user(db, value)
        if user is None:
            raise AbuseAdminError("Пользователь Telegram не найден в базе")
        username = user.telegram_username or str(user.telegram_id)
        return ResolvedBanTarget(
            ips=[],
            telegram_ids=[user.telegram_id],
            label=f"@{username.lstrip('@')}",
        )

    raise AbuseAdminError("Не удалось распознать цель: укажите IPv4, Telegram ID или @username")


def list_abuse_blocks(db: Session) -> dict[str, object]:
    ip_blocks = list_ip_blocks()
    telegram_bans = list_telegram_bans()
    telegram_ids = {item.telegram_id for item in telegram_bans}
    users_by_telegram = {}
    if telegram_ids:
        users = db.query(User).filter(User.telegram_id.in_(telegram_ids)).all()
        users_by_telegram = {user.telegram_id: user for user in users}

    return {
        "ip_blocks": [_serialize_ip_block(item) for item in ip_blocks],
        "telegram_bans": [
            _serialize_telegram_ban(item, users_by_telegram.get(item.telegram_id))
            for item in telegram_bans
        ],
    }


def create_abuse_ban(
    db: Session,
    admin: User,
    settings: Settings,
    *,
    target: str,
    duration_seconds: int,
    reason: str,
    ban_ip: bool,
    ban_account: bool,
) -> dict[str, object]:
    if duration_seconds < 60:
        raise AbuseAdminError("Минимальная длительность блокировки — 60 секунд")
    if duration_seconds > 10 * 365 * 86400:
        raise AbuseAdminError("Слишком большая длительность блокировки")

    resolved = resolve_ban_target(db, target)
    created_by = _admin_label(admin)
    reason_text = reason.strip() or "manual ban"
    created: list[dict[str, object]] = []

    for telegram_id in resolved.telegram_ids:
        if is_admin_telegram_id(telegram_id, settings):
            raise AbuseAdminError("Нельзя заблокировать администратора сервиса")
        if ban_account:
            set_telegram_ban(
                telegram_id,
                duration_seconds,
                source="manual",
                reason=reason_text,
                created_by=created_by,
            )
            created.append({"kind": "telegram", "target": str(telegram_id)})
        if ban_ip:
            last_ip = get_user_last_ip(telegram_id)
            if last_ip:
                set_ip_block(
                    last_ip,
                    duration_seconds,
                    source="manual",
                    reason=f"{reason_text} (IP пользователя {resolved.label})",
                    created_by=created_by,
                )
                created.append({"kind": "ip", "target": last_ip})

    for ip in resolved.ips:
        set_ip_block(
            ip,
            duration_seconds,
            source="manual",
            reason=reason_text,
            created_by=created_by,
        )
        created.append({"kind": "ip", "target": ip})

    if not created:
        raise AbuseAdminError(
            "Нечего блокировать: для пользователя не найден последний IP — укажите IP напрямую "
            "или дождитесь, пока пользователь зайдёт на сайт"
        )

    return {"created": created, "label": resolved.label}


def delete_ip_block(ip: str) -> None:
    if not is_valid_ip(ip):
        raise AbuseAdminError("Некорректный IP")
    if not remove_ip_block(ip):
        raise AbuseAdminError("IP-блокировка не найдена", 404)


def delete_telegram_ban(telegram_id: int, settings: Settings) -> None:
    if is_admin_telegram_id(telegram_id, settings):
        raise AbuseAdminError("Нельзя снять блокировку с администратора через API")
    if not remove_telegram_ban(telegram_id):
        raise AbuseAdminError("Блокировка аккаунта не найдена", 404)


def clear_ip_score(ip: str) -> None:
    if not is_valid_ip(ip):
        raise AbuseAdminError("Некорректный IP")
    clear_ip_abuse_score(ip)


def get_ip_block_details(ip: str) -> dict[str, object]:
    if not is_valid_ip(ip):
        raise AbuseAdminError("Некорректный IP")
    record = get_ip_block_record(ip)
    if record is None:
        raise AbuseAdminError("IP-блокировка не найдена", 404)
    return _serialize_ip_block(record)


def _serialize_ip_block(record: IpBlockRecord) -> dict[str, object]:
    return {
        "ip": record.ip,
        "ttl_seconds": record.ttl_seconds,
        "score": record.score,
        "source": record.source,
        "reason": record.reason,
        "created_at": record.created_at,
        "created_by": record.created_by,
    }


def _serialize_telegram_ban(record: TelegramBanRecord, user: User | None) -> dict[str, object]:
    return {
        "telegram_id": record.telegram_id,
        "telegram_username": user.telegram_username if user else None,
        "display_name": user.display_name if user else None,
        "last_ip": record.last_ip,
        "ttl_seconds": record.ttl_seconds,
        "source": record.source,
        "reason": record.reason,
        "created_at": record.created_at,
        "created_by": record.created_by,
    }
