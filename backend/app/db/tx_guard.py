"""Сторож простоя транзакции.

На проде у соединений `idle_in_transaction_session_timeout=60s`
(`app/db/session.py`): транзакцию, в которой минуту не было ни одного запроса,
Postgres рвёт вместе с соединением. Падает при этом СЛЕДУЮЩИЙ запрос или
COMMIT (`IdleInTransactionSessionTimeout`), и по ошибке не видно, где на самом
деле простаивали — обычно это поход в сеть (5 вёрст, S95, Nominatim, Telegram,
Open-Meteo) при открытой транзакции. Открывает её любой SELECT, в том числе
чтение «протухшего» поля ORM-объекта после commit. 19.09.2026 так упала
сверка реестра 5 вёрст.

Сторож меряет на уровне движка паузу между запросами внутри транзакции и,
если она больше порога (по умолчанию 30 с — вдвое раньше обрыва), называет
место, откуда пришёл запрос после паузы: на проде — предупреждение в лог с
коротким стеком (не чаще раза в час на место), под pytest — AssertionError,
чтобы тест на таком месте падал.

Лечение — закрыть транзакцию перед сетью: `release_before_fetch` /
`commit_step` (`app/sync/iteration_commit.py`), в запросах API —
`release_db_connection_for_network_io`.

Включение и порог — ручки движка, их читает `app/db/session.py`:
`DB_TX_GUARD_DISABLED=1` — выключить (в том числе на время отладки теста в pdb),
`DB_TX_GUARD_IDLE_SECONDS` — порог в секундах.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import traceback
import weakref
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine

from app.core.runtime_env import is_test_run

logger = logging.getLogger(__name__)

DEFAULT_IDLE_SECONDS = 30.0
# Одно место на проде повторяется каждым прогоном синка — в лог раз в час.
WARN_EVERY_SECONDS = 3600.0
STACK_FRAMES = 8

# conn.info живёт вместе с DBAPI-соединением пула; begin новой транзакции
# перезаписывает отметку, так что прошлые checkout'ы не мешают.
LAST_SQL_KEY = "srs_last_sql_at"
# Транзакция открыта, а запросов в ней ещё не было — для текста предупреждения.
FRESH_TX_KEY = "srs_tx_fresh"

_THIS_FILE = os.path.abspath(__file__)
_APP_DIR = os.path.dirname(os.path.dirname(_THIS_FILE))
_BACKEND_DIR = os.path.dirname(_APP_DIR)

_GUARDS: weakref.WeakKeyDictionary[Engine, TxGuard] = weakref.WeakKeyDictionary()


def _autocommit(conn: Connection) -> bool:
    # В AUTOCOMMIT SQLAlchemy всё равно считает соединение «в транзакции», но
    # сервер её не держит, и таймаут простоя такое соединение не рвёт
    # (так живёт advisory-lock каталога parkrun на время всего прогона).
    return conn.get_execution_options().get("isolation_level") == "AUTOCOMMIT"


def _own_frames() -> list[tuple[str, int, str]]:
    """Короткий стек: кадры из app/, а если их нет (скрипт, тест) — из backend/."""
    frames: list[tuple[str, int, str]] = []
    for frame in traceback.extract_stack():
        if frame.filename.startswith("<"):  # <frozen …>, <string>
            continue
        path = os.path.abspath(frame.filename)
        if path == _THIS_FILE or "site-packages" in path:
            continue
        frames.append((path, frame.lineno or 0, frame.name))
    app_frames = [f for f in frames if f[0].startswith(_APP_DIR + os.sep)]
    if app_frames:
        return app_frames[-STACK_FRAMES:]
    return [f for f in frames if f[0].startswith(_BACKEND_DIR + os.sep)][-STACK_FRAMES:]


def _short_sql(statement: str) -> str:
    text = " ".join(statement.split())
    return text if len(text) <= 160 else text[:157] + "…"


class TxGuard:
    """Обработчики событий движка; порог и журнал предупреждений — на движок."""

    def __init__(self, idle_seconds: float) -> None:
        self.idle_seconds = idle_seconds
        self._warned_at: dict[tuple[str, int], float] = {}
        self._lock = threading.Lock()

    def on_begin(self, conn: Connection) -> None:
        conn.info[LAST_SQL_KEY] = time.monotonic()
        conn.info[FRESH_TX_KEY] = True

    def before_cursor_execute(
        self, conn: Connection, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        self._check(conn, statement)

    def after_cursor_execute(
        self, conn: Connection, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        # Долгий запрос — не простой: считаем от его конца.
        conn.info[LAST_SQL_KEY] = time.monotonic()

    def on_commit(self, conn: Connection) -> None:
        # Транзакция без единого запроса на сервере не начиналась (psycopg шлёт
        # BEGIN вместе с первым запросом), и COMMIT ничего не отправляет.
        if conn.info.get(FRESH_TX_KEY):
            return
        self._check(conn, "COMMIT")

    def _check(self, conn: Connection, statement: str) -> None:
        info = conn.info
        now = time.monotonic()
        last = info.get(LAST_SQL_KEY)
        since_begin = info.pop(FRESH_TX_KEY, False)
        # Отметку ставим до проверки: упавший запрос не должен тянуть ту же
        # паузу в следующий (откат, teardown теста).
        info[LAST_SQL_KEY] = now
        if last is None or now - last <= self.idle_seconds:
            return
        if not conn.in_transaction() or _autocommit(conn):
            return
        self._report(now - last, statement, since_begin=since_begin, now=now)

    def _report(self, idle: float, statement: str, *, since_begin: bool, now: float) -> None:
        if is_test_run():
            raise AssertionError(self._message(idle, statement, _own_frames(), since_begin=since_begin))
        # На проде сторож не должен ломать запрос ни при каких условиях.
        try:
            frames = _own_frames()
            key = (frames[-1][0], frames[-1][1]) if frames else ("?", 0)
            with self._lock:
                warned = self._warned_at.get(key)
                if warned is not None and now - warned < WARN_EVERY_SECONDS:
                    return
                self._warned_at[key] = now
            logger.warning(self._message(idle, statement, frames, since_begin=since_begin))
        except Exception:  # noqa: BLE001
            logger.debug("сторож простоя транзакции не смог собрать отчёт", exc_info=True)

    @staticmethod
    def _message(idle: float, statement: str, frames: list[tuple[str, int, str]], *, since_begin: bool) -> str:
        what = "COMMIT" if statement == "COMMIT" else "SQL"
        pause = "с начала транзакции без единого запроса" if since_begin else "простоя внутри транзакции"
        stack = "\n".join(f"  {os.path.relpath(path, _BACKEND_DIR)}:{line} in {name}" for path, line, name in frames)
        return (
            f"{what} после {idle:.0f} с {pause} — Postgres рвёт на 60 с "
            "(idle_in_transaction_session_timeout), и тогда этот запрос падает с "
            "IdleInTransactionSessionTimeout. Закройте транзакцию перед сетью: "
            "release_before_fetch / commit_step, в API — release_db_connection_for_network_io. "
            f"Запрос: {_short_sql(statement)}\nСтек:\n{stack or '  (своих кадров нет)'}"
        )


def install_tx_guard(engine: Engine, *, idle_seconds: float = DEFAULT_IDLE_SECONDS) -> TxGuard:
    """Повесить сторож на движок (повторный вызов возвращает уже стоящий)."""
    existing = _GUARDS.get(engine)
    if existing is not None:
        return existing
    guard = TxGuard(idle_seconds)
    event.listen(engine, "begin", guard.on_begin)
    event.listen(engine, "before_cursor_execute", guard.before_cursor_execute)
    event.listen(engine, "after_cursor_execute", guard.after_cursor_execute)
    event.listen(engine, "commit", guard.on_commit)
    _GUARDS[engine] = guard
    return guard


def guard_for(engine: Engine) -> TxGuard | None:
    return _GUARDS.get(engine)
