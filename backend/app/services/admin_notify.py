"""Единая точка admin-уведомлений от фич сайта.

Канал тот же, что у суточной сводки автообновления: Telegram через прокси, а ВК
— только фолбэк, когда прокси легла (см. `admin_telegram_notify.send_admin_report`).
До 03.08.2026 бэклог слал напрямую в ВК.

На прогоне тестов уведомления не уходят никуда: pytest на локальном стеке
подхватывает боевой .env, и тесты бэклога прилетали админу живыми сообщениями
(«Новая карточка бэклога: [фича] «Идея»», «Комментарий … Первый»).
"""

from __future__ import annotations

import logging

from app.core.runtime_env import is_test_run
from app.services.admin_telegram_notify import send_admin_report

logger = logging.getLogger(__name__)


def notify_admin(text: str) -> None:
    if is_test_run():
        logger.info("Admin notify skipped: test run (%s)", text[:80])
        return
    send_admin_report(text)
