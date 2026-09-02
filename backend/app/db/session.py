import os
from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def _engine_connect_args() -> dict[str, object]:
    server_opts: list[str] = []

    # idle_in_transaction_session_timeout: Postgres сам закрывает соединение,
    # зависшее "idle in transaction" (утёкшая сессия), — иначе оно держит слот
    # пула и коннект навсегда (см. инцидент 04.07.2026: логин лёг из-за 8 таких).
    #
    # НО parkrun/S95 Mac-демон легитимно держит транзакцию открытой во время
    # 60-90s браузерного фетча: process_pending_row коммитит статус 'processing',
    # затем _get_platform() открывает новую транзакцию, которая простаивает весь
    # фетч. 60s-таймаут убивал коннект ровно в момент записи результата — успешно
    # спарсенный профиль не доходил до 'done', а строка зависала в 'processing'.
    # Для такого клиента (одно соединение, один прогон, локально) таймаут отключаем.
    if os.environ.get("DB_DISABLE_IDLE_TX_TIMEOUT") != "1":
        server_opts.append("-c idle_in_transaction_session_timeout=60s")

    if os.environ.get("PROD_DB_TARGET") == "1":
        # Локальные прогоны на prod: не висеть бесконечно на lock от worker-five-verst.
        server_opts += ["-c lock_timeout=30s", "-c statement_timeout=600s"]

    # Потолок на один запрос — для веб-процесса. Ни одна страница не считается
    # минутами, а вот сорваться в такой запрос она может: 03.09.2026 панель
    # дебютантов в кабинете организатора собирала первую пробежку КАЖДОГО
    # участника, шла 30-70 минут, накопила 37 одновременных копий и выела пул
    # соединений — сайт отвечал «QueuePool limit of size 5 overflow 10 reached».
    # С потолком такой запрос умирает сам и слот в пуле не держит.
    # Воркерам таймаут НЕ ставим: там пересчёты рекордов и импорты легитимно
    # идут долго, поэтому переменная задаётся только сервису api.
    statement_timeout = os.environ.get("DB_STATEMENT_TIMEOUT", "").strip()
    if statement_timeout:
        server_opts.append(f"-c statement_timeout={statement_timeout}")

    if not server_opts:
        return {}
    return {"options": " ".join(server_opts)}


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    kwargs: dict[str, object] = {
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 10,
        "pool_recycle": 300,
        "pool_timeout": 10,
        "connect_args": _engine_connect_args(),
    }
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


@lru_cache
def get_report_engine() -> Engine:
    """Engine для read-only reporting API.

    Использует ``report_database_url`` (отдельная роль с GRANT SELECT), если он
    задан, иначе — обычный ``database_url``. Пул маленький: отчёты редкие, но не
    должны отъедать слоты у основного приложения.
    """
    settings = get_settings()
    url = settings.report_database_url or settings.database_url
    kwargs: dict[str, object] = {
        "pool_pre_ping": True,
        "pool_size": 2,
        "max_overflow": 3,
        "pool_recycle": 300,
        "pool_timeout": 10,
        "connect_args": _engine_connect_args(),
    }
    return create_engine(url, **kwargs)


@lru_cache
def get_report_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_report_engine(), autocommit=False, autoflush=False)


def get_report_db() -> Generator[Session, None, None]:
    db = get_report_session_factory()()
    try:
        yield db
    finally:
        db.close()


def release_db_connection_for_network_io(db: Session) -> None:
    """Вернуть коннект сессии в пул перед долгим сетевым вызовом.

    `Depends(get_db)` выдаёт коннект на весь запрос, и живой фетч parkrun/s95/5v
    (rate-limit ожидание + сам запрос, до десятков секунд) держал бы слот пула
    всё это время — несколько параллельных привязок профиля исчерпывают пул
    целиком (инцидент 16.07.2026). rollback() завершает транзакцию и возвращает
    коннект в пул, но в отличие от close() сессия и её объекты остаются
    привязанными — следующее обращение к БД прозрачно возьмёт новый коннект.

    Вызывать ТОЛЬКО когда в транзакции нет незакоммиченных изменений: rollback
    их молча отбросит.
    """
    if isinstance(db.bind, Connection):
        # Сессия привязана к внешнему Connection (так работает тестовый
        # SAVEPOINT-паттерн в conftest): коннект принадлежит не сессии,
        # возвращать в пул нечего, а rollback() откатил бы внешнюю
        # транзакцию вместе с данными вызывающего.
        return
    db.rollback()
