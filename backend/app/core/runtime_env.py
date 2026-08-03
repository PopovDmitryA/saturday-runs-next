"""Признаки окружения выполнения.

Нужен, чтобы внешние отправки (сейчас — admin-уведомления) молчали на прогоне
тестов: pytest на локальном стеке подхватывает тот же .env с боевыми токенами,
и каждый прогон тестов бэклога уходил живыми сообщениями админу.
"""

from __future__ import annotations

import os

from app.config import get_settings

_TEST_ENV_NAMES = {"test", "testing", "pytest"}


def is_test_run() -> bool:
    """True под pytest (переменная выставляется на каждом тесте) или при APP_ENV=test."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return get_settings().app_env.strip().lower() in _TEST_ENV_NAMES
