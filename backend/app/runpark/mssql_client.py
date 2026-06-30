from __future__ import annotations

import logging
import threading
from typing import Any

import pymssql

from app.config import get_settings

logger = logging.getLogger(__name__)

# One MSSQL connection is reused across queries instead of reconnecting per call.
# pymssql connections are not thread-safe, so the connection is kept per-thread:
# the worker (concurrency=1) and each FastAPI threadpool thread get their own,
# long-lived connection. A fresh connection is created only on the first query of
# a thread or after a previous one died (see runpark_query).
_local = threading.local()


def _connect() -> pymssql.Connection:
    s = get_settings()
    return pymssql.connect(
        server=s.runpark_mssql_server,
        database=s.runpark_mssql_database,
        user=s.runpark_mssql_user,
        password=s.runpark_mssql_password,
        timeout=s.runpark_mssql_timeout,
        login_timeout=15,
        # Read-only access — avoid leaving a transaction open on the reused connection.
        autocommit=True,
    )


def _get_conn() -> pymssql.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


def _drop_conn() -> None:
    conn = getattr(_local, "conn", None)
    _local.conn = None
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — best-effort cleanup of a dead connection
            pass


def close_runpark_connection() -> None:
    """Close this thread's cached RunPark connection, if any. Optional cleanup hook."""
    _drop_conn()


def fix_varchar_encoding(s: str | None) -> str | None:
    """Fix VARCHAR columns stored as CP1251 but decoded as latin-1 by pymssql."""
    if s is None:
        return None
    try:
        return s.encode("latin-1").decode("cp1251")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def runpark_query(sql: str, params: tuple[Any, ...] | None = None, retries: int = 2) -> list[dict[str, Any]]:
    """Execute a single query against RunPark MSSQL.

    Reuses one connection per thread across calls; only reconnects when the cached
    connection has died (network/operational/interface error), retrying on failure.
    """
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            conn = _get_conn()
            cur = conn.cursor(as_dict=True)
            cur.execute(sql, params)
            return cur.fetchall()
        except (pymssql.OperationalError, pymssql.InterfaceError) as exc:
            # Connection is likely stale/dead — drop it so the next attempt reconnects.
            last_exc = exc
            _drop_conn()
            if attempt < retries:
                logger.warning("RunPark MSSQL network error (attempt %d/%d): %s", attempt + 1, retries + 1, exc)
            continue
    raise last_exc  # type: ignore[misc]
