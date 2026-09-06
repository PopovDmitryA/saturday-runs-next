"""Журнал писем с кодом: воронка «письмо ушло → человек вошёл».

Смысл всех проверок один: без журнала несостоявшийся вход по почте не
оставлял следа, и понять, сколько писем осело в спаме, было нечем.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import EmailLoginRequest
from app.services import email_auth_service, email_login_journal_service
from app.services.auth_service import AuthError


@pytest.fixture
def settings() -> Settings:
    return Settings(
        email_login_enabled=True,
        smtp_enabled=True,
        smtp_host="smtp.timeweb.ru",
        smtp_user="support@run5k.run",
        smtp_password="secret",
    )


@pytest.fixture
def sent_codes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    outbox: list[tuple[str, str]] = []

    def fake_queue(to: str, subject: str, text_body: str, html_body: str | None = None) -> bool:
        outbox.append((to, text_body))
        return True

    monkeypatch.setattr("app.workers.tasks.email_send.queue_email", fake_queue)
    return outbox


def _code_from(outbox: list[tuple[str, str]]) -> str:
    text = outbox[-1][1]
    digits = [word for word in text.replace("\n", " ").split() if word.isdigit() and len(word) == 6]
    return digits[0]


def _rows(db: Session, email: str) -> list[EmailLoginRequest]:
    from app.core import email_address

    return (
        db.query(EmailLoginRequest)
        .filter(
            EmailLoginRequest.email_hash
            == email_login_journal_service.mailbox_hash(email_address.normalize(email))
        )
        .order_by(EmailLoginRequest.id)
        .all()
    )


def test_sent_letter_is_recorded_and_login_marks_it_verified(
    db_session: Session,
    settings: Settings,
    sent_codes: list[tuple[str, str]],
) -> None:
    email = f"runner-{uuid4().hex[:8]}@gmail.com"

    email_auth_service.request_code(db_session, settings, email, client_ip="10.0.0.1", consent=True)

    rows = _rows(db_session, email)
    assert len(rows) == 1
    # Домен нормализован: письма на googlemail.com и gmail.com — один почтовик.
    assert rows[0].domain == "gmail.com"
    assert rows[0].purpose == "login"
    assert rows[0].verified_at is None

    email_auth_service.verify_code(db_session, settings, email, _code_from(sent_codes))

    db_session.expire_all()
    assert _rows(db_session, email)[0].verified_at is not None


def test_wrong_code_counts_as_letter_delivered(
    db_session: Session,
    settings: Settings,
    sent_codes: list[tuple[str, str]],
) -> None:
    """Неверный код — письмо человек всё-таки увидел.

    Именно это отличает «письмо не дошло» от «дошло, но не вошёл», и без
    этой отметки первое неотличимо от второго.
    """
    email = f"runner-{uuid4().hex[:8]}@yandex.ru"
    email_auth_service.request_code(db_session, settings, email, client_ip="10.0.0.1", consent=True)

    with pytest.raises(AuthError):
        email_auth_service.verify_code(db_session, settings, email, "000000")

    db_session.expire_all()
    assert _rows(db_session, email)[0].failed_attempts == 1


def test_undelivered_letter_leaves_no_row(
    db_session: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отказ отправки не должен выглядеть в отчёте как проблема доставки."""
    email = f"runner-{uuid4().hex[:8]}@mail.ru"

    monkeypatch.setattr("app.workers.tasks.email_send.queue_email", lambda *a, **k: False)

    def boom(*args: object, **kwargs: object) -> None:
        from app.core.mailer import MailerError

        raise MailerError("smtp down")

    monkeypatch.setattr("app.core.mailer.send_email", boom)

    with pytest.raises(AuthError):
        email_auth_service.request_code(
            db_session, settings, email, client_ip="10.0.0.1", consent=True
        )

    assert _rows(db_session, email) == []


def test_funnel_counts_mailboxes_not_letters(
    db_session: Session,
    settings: Settings,
    sent_codes: list[tuple[str, str]],
) -> None:
    """Человек, запросивший два кода и вошедший, — одна победа, а не две записи.

    Проверяем на паре ящиков одного домена: один вошёл со второго письма,
    второй не открыл письмо вовсе.
    """
    winner = f"winner-{uuid4().hex[:8]}@gmail.com"
    lost = f"lost-{uuid4().hex[:8]}@gmail.com"

    email_auth_service.request_code(db_session, settings, winner, client_ip="10.0.0.1", consent=True)
    email_auth_service.request_code(db_session, settings, winner, client_ip="10.0.0.1", consent=True)
    email_auth_service.request_code(db_session, settings, lost, client_ip="10.0.0.2", consent=True)
    email_auth_service.verify_code(db_session, settings, winner, _code_from(sent_codes[:-1]))

    funnel = email_login_journal_service.get_email_login_funnel(db_session, period_days=1)
    gmail = next(row for row in funnel["by_domain"] if row["domain"] == "gmail.com")

    assert gmail["requests"] == 3
    assert gmail["mailboxes"] == 2
    assert gmail["verified_mailboxes"] == 1
    assert gmail["conversion"] == 50.0
    # Второй ящик не ввёл ни одного кода: письмо до человека не дошло.
    assert gmail["silent_mailboxes"] == 1
