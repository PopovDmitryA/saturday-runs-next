"""Отправка писем через внешний SMTP.

Свой MTA на сервере сайта не поднимаем: письмо с IP без нормального PTR и без
репутации домена уходит в спам у mail.ru и gmail, а на домашнем сервере (куда
Дмитрий может переехать) исходящий 25-й порт вдобавок обычно закрыт оператором.
Поэтому шлём авторизованным клиентом через почтовый кластер, где домен уже
прописан — сейчас это Timeweb, ящик support@run5k.run.

Модуль намеренно тонкий: собрать письмо и отдать его SMTP-серверу. Кто и по
какому поводу пишет — дело вызывающего кода; повторные попытки — дело celery
(app/workers/tasks/email_send.py), чтобы веб-воркер не ждал сеть.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from app.config import Settings

logger = logging.getLogger(__name__)


class MailerError(Exception):
    """Письмо не ушло. Текст пригоден для лога, но не для показа человеку."""


def sender_email(settings: Settings) -> str:
    return settings.smtp_from_email.strip() or settings.smtp_user.strip()


def reply_to_email(settings: Settings) -> str:
    return settings.smtp_reply_to.strip() or sender_email(settings)


def is_configured(settings: Settings) -> bool:
    """Есть ли всё, чтобы вообще пытаться отправить."""
    return bool(
        settings.smtp_enabled
        and settings.smtp_host.strip()
        and settings.smtp_user.strip()
        and settings.smtp_password
        and sender_email(settings)
    )


def build_message(
    settings: Settings,
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> EmailMessage:
    """Собрать письмо.

    Текстовая версия обязательна, html — по желанию: часть почтовых клиентов
    (и все спам-фильтры) читают именно text/plain, и письмо без него выглядит
    подозрительнее. Message-ID и Date проставляем сами — без них некоторые
    приёмники штрафуют письмо в оценке спамности.
    """
    message = EmailMessage()
    message["From"] = formataddr((settings.smtp_from_name.strip() or None, sender_email(settings)))
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    from_domain = sender_email(settings).rsplit("@", 1)[-1] or None
    message["Message-ID"] = make_msgid(domain=from_domain)

    reply_to = reply_to_email(settings)
    if reply_to and reply_to != sender_email(settings):
        message["Reply-To"] = reply_to

    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    return message


def send_email(
    settings: Settings,
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> None:
    """Отправить письмо. Бросает MailerError, если не вышло."""
    if not is_configured(settings):
        raise MailerError("SMTP is not configured (smtp_enabled/host/user/password).")

    message = build_message(
        settings,
        to=to,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )

    try:
        with _connect(settings) as client:
            client.login(settings.smtp_user, settings.smtp_password)
            client.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        # OSError ловим вместе с SMTPException нарочно: закрытый исходящий порт
        # и оборванный коннект выглядят как обычная сетевая ошибка, а для
        # вызывающего это один и тот же исход — письмо не ушло.
        raise MailerError(f"SMTP send failed: {exc}") from exc

    # Тему в лог не пишем: у писем с кодом она содержит сам код, а логи живут
    # дольше и читаются шире, чем стоило бы одноразовому ключу от профиля.
    logger.info("mailer: sent message to %s", to)


def _connect(settings: Settings) -> smtplib.SMTP:
    timeout = settings.smtp_timeout_seconds
    if settings.smtp_use_ssl:
        return smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=timeout)
    client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout)
    client.starttls()
    return client
