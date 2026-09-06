"""Журнал писем с кодом входа и воронка «письмо ушло → человек вошёл».

Зачем: вход по почте упирается не в интерфейс, а в доставку. Письмо от
малознакомого отправителя почтовик кладёт в спам, человек его не находит и
уходит — а в базе от этого не остаётся ничего, кроме несостоявшегося входа.
Одна строка на письмо закрывает дыру: разрыв между числом писем и числом
входов и есть цена доставки.

Читать отчёт стоит по ЯЩИКАМ, а не по письмам: человек, запросивший три кода
и вошедший, — одна победа, а не три поражения и одна победа. Поэтому основная
конверсия считается как «ящиков вошло / ящиков запросило».

Домен получателя — главный срез. Если у gmail конверсия заметно ниже, чем у
яндекса, дело не в людях, а в том, что до Google письма не долетают; там же и
лечится (Postmaster Tools, прогрев, жалобы).

Журнал — диагностика, а не бизнес-логика: любая его поломка не должна ронять
сам вход, поэтому запись обёрнута в try/except.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import Date, case, cast, distinct, func, or_
from sqlalchemy.orm import Session

from app.core import email_address
from app.models import EmailLoginRequest

logger = logging.getLogger(__name__)

PURPOSE_LOGIN = "login"
PURPOSE_LINK = "link"


def mailbox_hash(normalized_email: str) -> str:
    """sha256 нормализованного ящика — то, что лежит в таблице вместо адреса.

    Функция публичная намеренно: чтобы найти письма конкретного человека по
    жалобе «код не пришёл», нужно посчитать хэш от его адреса.
    """
    return hashlib.sha256(normalized_email.encode("utf-8")).hexdigest()


def record_request(
    db: Session,
    *,
    normalized_email: str,
    purpose: str = PURPOSE_LOGIN,
    known_mailbox: bool = False,
    ip: str = "",
) -> int | None:
    """Записать отправленное письмо. Возвращает id строки (или None при сбое).

    Зовётся ПОСЛЕ успешной отправки: строка означает «письмо ушло», а не
    «человек нажал кнопку». Иначе отказ SMTP выглядел бы в отчёте как
    проблема доставки, хотя письма не было вовсе.
    """
    _, domain = email_address.split(normalized_email)
    try:
        row = EmailLoginRequest(
            email_hash=mailbox_hash(normalized_email),
            domain=domain[:64],
            purpose=purpose[:16],
            known_mailbox=known_mailbox,
            ip=(ip or "")[:64],
        )
        db.add(row)
        db.commit()
        return int(row.id)
    except Exception:  # noqa: BLE001 — журнал не должен ломать вход
        logger.exception("email login journal: failed to record request")
        db.rollback()
        return None


def discard_request(db: Session, request_id: int | None) -> None:
    """Убрать строку письма, которое так и не ушло (отказ SMTP).

    Иначе отказ отправки выглядел бы в отчёте как «письмо ушло и осталось без
    ответа», то есть как проблема доставки, которой не было.
    """
    if not request_id:
        return
    try:
        db.query(EmailLoginRequest).filter(EmailLoginRequest.id == request_id).delete(
            synchronize_session=False
        )
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("email login journal: failed to discard request %s", request_id)
        db.rollback()


def mark_verified(db: Session, request_id: int | None) -> None:
    """Отметить, что код именно из этого письма сработал."""
    if not request_id:
        return
    try:
        db.query(EmailLoginRequest).filter(
            EmailLoginRequest.id == request_id,
            EmailLoginRequest.verified_at.is_(None),
        ).update({"verified_at": datetime.now(timezone.utc)}, synchronize_session=False)
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("email login journal: failed to mark request %s verified", request_id)
        db.rollback()


def mark_failed_attempt(db: Session, normalized_email: str) -> None:
    """Плюс одна неверная попытка последнему письму этого ящика.

    Нужно ровно для одного различения: ноль попыток значит «письмо не нашли»,
    а попытки есть — письмо человек получил и споткнулся уже о код.
    """
    try:
        latest = (
            db.query(EmailLoginRequest.id)
            .filter(EmailLoginRequest.email_hash == mailbox_hash(normalized_email))
            .order_by(EmailLoginRequest.requested_at.desc())
            .limit(1)
            .scalar()
        )
        if latest is None:
            return
        db.query(EmailLoginRequest).filter(EmailLoginRequest.id == latest).update(
            {"failed_attempts": EmailLoginRequest.failed_attempts + 1},
            synchronize_session=False,
        )
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("email login journal: failed to count wrong code")
        db.rollback()


def purge_old_requests(db: Session, *, retention_days: int) -> int:
    """Чистка журнала по тому же сроку, что и журнал входов.

    Строка полезна ровно до тех пор, пока по ней считают воронку; хранить
    вечно хэш ящика и IP человека, который к нам так и не вошёл, незачем.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted = (
        db.query(EmailLoginRequest)
        .filter(EmailLoginRequest.requested_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return int(deleted)


def _rate(part: int, whole: int) -> float:
    return round(part * 100 / whole, 1) if whole else 0.0


def _verified() -> object:
    return EmailLoginRequest.verified_at.isnot(None)


def _engaged() -> object:
    """Признак «письмо человек увидел»: код был введён — верный или нет."""
    return or_(EmailLoginRequest.verified_at.isnot(None), EmailLoginRequest.failed_attempts > 0)


def get_email_login_funnel(db: Session, *, period_days: int = 30) -> dict[str, object]:
    """Воронка входа по почте за период.

    Считается только purpose='login': привязка почты в настройках идёт из
    профиля, где человек уже вошёл, и мешать её с входом нельзя — там другая
    мотивация искать письмо.
    """
    since = datetime.now(timezone.utc) - timedelta(days=period_days)
    base = db.query(EmailLoginRequest).filter(
        EmailLoginRequest.requested_at >= since,
        EmailLoginRequest.purpose == PURPOSE_LOGIN,
    )

    by_domain = [
        {
            "domain": row.domain or "—",
            "requests": int(row.requests),
            "mailboxes": int(row.mailboxes),
            "verified_mailboxes": int(row.verified_mailboxes),
            "conversion": _rate(int(row.verified_mailboxes), int(row.mailboxes)),
            # Ящики, откуда не пришло ни одной попытки ввода: письмо человек
            # так и не увидел. Это и есть верхняя оценка «ушло в спам».
            "silent_mailboxes": int(row.mailboxes) - int(row.engaged_mailboxes),
        }
        for row in base.with_entities(
            EmailLoginRequest.domain.label("domain"),
            func.count(EmailLoginRequest.id).label("requests"),
            func.count(distinct(EmailLoginRequest.email_hash)).label("mailboxes"),
            func.count(distinct(EmailLoginRequest.email_hash))
            .filter(_verified())
            .label("verified_mailboxes"),
            # Письмо дошло: код ввели — верный или нет.
            func.count(distinct(EmailLoginRequest.email_hash))
            .filter(_engaged())
            .label("engaged_mailboxes"),
        )
        .group_by(EmailLoginRequest.domain)
        .order_by(func.count(distinct(EmailLoginRequest.email_hash)).desc())
        .all()
    ]

    # Ящик живёт ровно в одном домене (адрес нормализован), поэтому итог —
    # просто сумма строк по доменам: пересечений между ними нет.
    totals = {
        "requests": sum(row["requests"] for row in by_domain),
        "mailboxes": sum(row["mailboxes"] for row in by_domain),
        "verified_mailboxes": sum(row["verified_mailboxes"] for row in by_domain),
        "silent_mailboxes": sum(row["silent_mailboxes"] for row in by_domain),
    }
    totals["lost_mailboxes"] = totals["mailboxes"] - totals["verified_mailboxes"]
    totals["conversion"] = _rate(totals["verified_mailboxes"], totals["mailboxes"])
    totals["silent_share"] = _rate(totals["silent_mailboxes"], totals["mailboxes"])

    known_split = {
        ("known" if bool(row.known_mailbox) else "new"): {
            "mailboxes": int(row.mailboxes),
            "verified_mailboxes": int(row.verified_mailboxes),
            "conversion": _rate(int(row.verified_mailboxes), int(row.mailboxes)),
        }
        for row in base.with_entities(
            EmailLoginRequest.known_mailbox.label("known_mailbox"),
            func.count(distinct(EmailLoginRequest.email_hash)).label("mailboxes"),
            func.count(distinct(EmailLoginRequest.email_hash))
            .filter(_verified())
            .label("verified_mailboxes"),
        )
        .group_by(EmailLoginRequest.known_mailbox)
        .all()
    }

    # Сколько ящиков просили код не один раз: обычно это «письмо не нашёл,
    # попробую ещё» — прямой признак проблем с доставкой.
    repeat_mailboxes = (
        db.query(func.count())
        .select_from(
            base.with_entities(EmailLoginRequest.email_hash)
            .group_by(EmailLoginRequest.email_hash)
            .having(func.count(EmailLoginRequest.id) > 1)
            .subquery()
        )
        .scalar()
        or 0
    )

    by_day = [
        {
            "date": row.day.isoformat(),
            "requests": int(row.requests),
            "verified": int(row.verified),
        }
        for row in base.with_entities(
            cast(EmailLoginRequest.requested_at, Date).label("day"),
            func.count(EmailLoginRequest.id).label("requests"),
            func.count(case((EmailLoginRequest.verified_at.isnot(None), 1))).label("verified"),
        )
        .group_by(cast(EmailLoginRequest.requested_at, Date))
        .order_by(cast(EmailLoginRequest.requested_at, Date))
        .all()
    ]

    return {
        "period_days": period_days,
        "generated_at": datetime.now(timezone.utc),
        "totals": {
            **totals,
            "repeat_mailboxes": int(repeat_mailboxes),
            "new": known_split.get("new", {"mailboxes": 0, "verified_mailboxes": 0, "conversion": 0.0}),
            "known": known_split.get("known", {"mailboxes": 0, "verified_mailboxes": 0, "conversion": 0.0}),
        },
        "by_domain": by_domain,
        "by_day": by_day,
    }
