from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.services.parkrun_legacy_seed import legacy_database_url


def get_legacy_engine() -> Engine:
    url = legacy_database_url()
    if not url:
        raise RuntimeError(
            "Задайте LEGACY_DATABASE_URL (read-only) или DATABASE_URL с saturday_runs_lk "
            "— тогда будет использована five_verst_stats на том же хосте."
        )
    return create_engine(url, pool_pre_ping=True)


@contextmanager
def legacy_connection(engine: Engine | None = None) -> Iterator[Any]:
    owns_engine = engine is None
    engine = engine or get_legacy_engine()
    try:
        with engine.connect() as conn:
            yield conn
    finally:
        if owns_engine:
            engine.dispose()


def legacy_scalar(conn: Any, sql: str, params: Mapping[str, object] | None = None) -> Any:
    return conn.execute(text(sql), params or {}).scalar()


def legacy_rows(
    conn: Any,
    sql: str,
    params: Mapping[str, object] | None = None,
) -> list[Mapping[str, Any]]:
    return conn.execute(text(sql), params or {}).mappings().all()


def legacy_row_stream(
    conn: Any,
    sql: str,
    params: Mapping[str, object] | None = None,
    *,
    chunk_size: int = 5000,
) -> Iterator[Mapping[str, Any]]:
    result = conn.execution_options(stream_results=True).execute(text(sql), params or {})
    while True:
        rows = result.fetchmany(chunk_size)
        if not rows:
            break
        for row in rows:
            yield row._mapping
