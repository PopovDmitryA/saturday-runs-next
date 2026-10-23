"""Журналы без секретов в адресах запросов.

httpx на уровне INFO пишет каждый запрос строкой «HTTP Request: POST <адрес>».
У Telegram Bot API токен бота — часть адреса (/bot<токен>/sendMessage), и до
23.10.2026 он лежал в журнале worker открытым текстом (26 строк за двое суток).
Этим же токеном проверяется вход через виджет Telegram — утёкший токен даёт
войти под любым человеком. Ответы с ошибками (WARNING и выше) остаются.
"""

from __future__ import annotations

import logging

_HTTP_CLIENT_LOGGERS = ("httpx", "httpcore")


def quiet_http_client_logs() -> None:
    for name in _HTTP_CLIENT_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
