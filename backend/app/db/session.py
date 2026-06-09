import os
from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def _engine_connect_args() -> dict[str, object]:
    if os.environ.get("PROD_DB_TARGET") != "1":
        return {}
    # Локальные прогоны на prod: не висеть бесконечно на lock от worker-five-verst.
    return {"options": "-c lock_timeout=30s -c statement_timeout=600s"}


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    connect_args = _engine_connect_args()
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if connect_args:
        kwargs["connect_args"] = connect_args
    return create_engine(settings.database_url, **kwargs)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
