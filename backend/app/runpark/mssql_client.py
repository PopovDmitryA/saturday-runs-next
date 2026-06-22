from __future__ import annotations

import logging
from typing import Any

import pymssql

from app.config import get_settings

logger = logging.getLogger(__name__)


def _connect() -> pymssql.Connection:
    s = get_settings()
    return pymssql.connect(
        server=s.runpark_mssql_server,
        database=s.runpark_mssql_database,
        user=s.runpark_mssql_user,
        password=s.runpark_mssql_password,
        timeout=s.runpark_mssql_timeout,
        login_timeout=15,
    )


def fix_varchar_encoding(s: str | None) -> str | None:
    """Fix VARCHAR columns stored as CP1251 but decoded as latin-1 by pymssql."""
    if s is None:
        return None
    try:
        return s.encode("latin-1").decode("cp1251")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def runpark_query(sql: str, params: tuple[Any, ...] | None = None, retries: int = 2) -> list[dict[str, Any]]:
    """Execute a single query against RunPark MSSQL. Reconnects each call, retries on network errors."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            conn = _connect()
            try:
                cur = conn.cursor(as_dict=True)
                cur.execute(sql, params)
                return cur.fetchall()
            finally:
                conn.close()
        except pymssql.OperationalError as exc:
            last_exc = exc
            if attempt < retries:
                logger.warning("RunPark MSSQL network error (attempt %d/%d): %s", attempt + 1, retries + 1, exc)
            continue
    raise last_exc  # type: ignore[misc]
