"""Отправка писем фоном (очередь celery по умолчанию).

SMTP — это сеть с чужим таймаутом: держать на ней веб-воркер, пока человек
смотрит на крутилку, нельзя. Поэтому роут кладёт письмо в очередь и сразу
отвечает, а доставкой занимается воркер.

Повторы: три попытки с растущей паузой. Почтовый кластер иногда отвечает
временной ошибкой (4xx), и одна неудача не повод терять письмо с кодом входа.
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.core.mailer import MailerError, send_email
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="email_send.deliver",
    autoretry_for=(MailerError,),
    retry_backoff=5,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def deliver(to: str, subject: str, text_body: str, html_body: str | None = None) -> str:
    settings = get_settings()
    send_email(
        settings,
        to=to,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )
    return "sent"


def queue_email(to: str, subject: str, text_body: str, html_body: str | None = None) -> bool:
    """Поставить письмо в очередь.

    Возвращает False, если брокер недоступен: вызывающий сам решает, сказать ли
    человеку «письмо не ушло» или попробовать отправить синхронно. Падать здесь
    нельзя — недоступность Redis не должна оборачиваться 500-й на входе.
    """
    try:
        deliver.delay(to, subject, text_body, html_body)
    except Exception:  # noqa: BLE001 — брокер лежит, письмо важнее исключения
        logger.exception("mailer: failed to queue message %r to %s", subject, to)
        return False
    return True
