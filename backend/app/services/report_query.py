"""Read-only reporting queries for the internal reports API.

Позволяет выполнять произвольные `SELECT`/`WITH` запросы к БД в режиме
"только чтение". Основная гарантия безопасности — транзакция
``SET TRANSACTION READ ONLY`` (Postgres сам блокирует любые записи и DDL) плюс
``statement_timeout``. Рекомендуется дополнительно ходить под отдельной ролью с
``GRANT SELECT`` — см. ``.env.example`` (REPORT_API_*).

Структурные проверки ниже (одиночный statement, ведущий SELECT/WITH, блок-лист
пишущих ключевых слов) — это defense-in-depth, чтобы отдавать понятную 400-ошибку
вместо сырой ошибки БД. Литералы и комментарии вырезаются перед проверкой, чтобы
не ловить ложные срабатывания на строках вида ``WHERE name = 'update'``.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


class ReportQueryError(ValueError):
    """Запрос не прошёл валидацию или упал в БД (отдаётся как HTTP 400)."""


@dataclass(frozen=True)
class NamedReport:
    name: str
    description: str
    sql: str


# Курируемые именованные отчёты. Все — schema-independent (pg_catalog), поэтому
# безопасны на любой версии схемы. Добавляй сюда частые прикладные запросы.
_NAMED_REPORTS_LIST: list[NamedReport] = [
    NamedReport(
        name="db_size",
        description="Размер текущей базы данных.",
        sql="SELECT pg_size_pretty(pg_database_size(current_database())) AS size",
    ),
    NamedReport(
        name="table_sizes",
        description="Таблицы схемы public по убыванию полного размера.",
        sql=(
            "SELECT c.relname AS table, "
            "pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size, "
            "pg_total_relation_size(c.oid) AS total_bytes, "
            "c.reltuples::bigint AS approx_rows "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r' AND n.nspname = 'public' "
            "ORDER BY pg_total_relation_size(c.oid) DESC"
        ),
    ),
    NamedReport(
        name="connections",
        description="Открытые коннекты к текущей БД, сгруппированные по состоянию.",
        sql=(
            "SELECT state, count(*) AS connections FROM pg_stat_activity "
            "WHERE datname = current_database() GROUP BY state "
            "ORDER BY connections DESC"
        ),
    ),
    NamedReport(
        name="schema",
        description=(
            "Все колонки таблиц схемы public: тип, nullable, default. "
            "Даёт полную карту хранения для составления ad-hoc запросов."
        ),
        sql=(
            "SELECT c.table_name, c.ordinal_position, c.column_name, "
            "c.data_type, c.udt_name, c.character_maximum_length, "
            "c.is_nullable, c.column_default "
            "FROM information_schema.columns c "
            "JOIN information_schema.tables t "
            "  ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
            "WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE' "
            "ORDER BY c.table_name, c.ordinal_position"
        ),
    ),
    NamedReport(
        name="foreign_keys",
        description="Внешние ключи схемы public: какая колонка ссылается на какую таблицу/колонку.",
        sql=(
            "SELECT tc.table_name, kcu.column_name, "
            "ccu.table_name AS references_table, "
            "ccu.column_name AS references_column, tc.constraint_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name "
            "  AND tc.table_schema = kcu.table_schema "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON ccu.constraint_name = tc.constraint_name "
            "  AND ccu.table_schema = tc.table_schema "
            "WHERE tc.constraint_type = 'FOREIGN KEY' "
            "  AND tc.table_schema = 'public' "
            "ORDER BY tc.table_name, kcu.column_name"
        ),
    ),
    NamedReport(
        name="indexes",
        description="Индексы схемы public (помогает писать эффективные запросы).",
        sql=(
            "SELECT tablename AS table_name, indexname AS index_name, indexdef "
            "FROM pg_indexes WHERE schemaname = 'public' "
            "ORDER BY tablename, indexname"
        ),
    ),
]

NAMED_REPORTS: dict[str, NamedReport] = {r.name: r for r in _NAMED_REPORTS_LIST}


_LEADING_KEYWORD = re.compile(r"^\s*(with|select)\b", re.IGNORECASE)
# Пишущие / DDL ключевые слова. READ ONLY-транзакция и так их заблокирует
# (в т.ч. data-modifying CTE вида `WITH x AS (DELETE ...)`), но так ошибка чище.
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|"
    r"merge|refresh|reindex|cluster|vacuum|comment|call|do|copy|"
    r"prepare|execute|discard|listen|notify)\b",
    re.IGNORECASE,
)


def _scrub(sql: str) -> str:
    """Вырезать комментарии и строковые/идентификаторные литералы.

    Возвращённый текст используется ТОЛЬКО для проверок; исполняется оригинал.
    """
    # Блочные комментарии /* ... */
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    # Строчные комментарии -- ...
    sql = re.sub(r"--[^\n]*", " ", sql)
    # Dollar-quoted строки $tag$ ... $tag$
    sql = re.sub(r"\$([A-Za-z_]*)\$.*?\$\1\$", "''", sql, flags=re.DOTALL)
    # Одинарные кавычки (с учётом '' экранирования)
    sql = re.sub(r"'(?:''|[^'])*'", "''", sql)
    # Двойные кавычки — идентификаторы
    sql = re.sub(r'"(?:""|[^"])*"', '""', sql)
    return sql


def validate_sql(sql: str) -> str:
    """Проверить, что это одиночный read-only SELECT/WITH. Вернуть очищенный SQL.

    Raises:
        ReportQueryError: если запрос пустой, многооператорный, не SELECT/WITH,
            либо содержит пишущее ключевое слово.
    """
    normalized = sql.strip().rstrip(";").strip()
    if not normalized:
        raise ReportQueryError("Пустой запрос")

    scrubbed = _scrub(normalized)
    if ";" in scrubbed:
        raise ReportQueryError("Разрешён только один запрос (без ';')")
    if not _LEADING_KEYWORD.match(scrubbed):
        raise ReportQueryError("Разрешены только SELECT / WITH запросы")
    forbidden = _FORBIDDEN.search(scrubbed)
    if forbidden:
        raise ReportQueryError(
            f"Запрещённое ключевое слово '{forbidden.group(0).lower()}' — только read-only SELECT"
        )
    return normalized


@dataclass
class ReportResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    elapsed_ms: int


def run_read_only_query(
    db: Session,
    sql: str,
    *,
    limit: int,
    timeout_seconds: int,
) -> ReportResult:
    """Выполнить read-only запрос и вернуть строки (не более ``limit``).

    Гарантии: ``SET TRANSACTION READ ONLY`` (первым в транзакции) + LOCAL
    ``statement_timeout``. Сессия по завершении откатывается (данных на запись
    нет), возвращая коннект в пул.

    Raises:
        ReportQueryError: валидация не прошла или запрос упал в БД.
    """
    normalized = validate_sql(sql)
    if limit < 1:
        raise ReportQueryError("limit должен быть >= 1")

    timeout_ms = max(1, int(timeout_seconds)) * 1000
    try:
        # READ ONLY должен идти до первого запроса в транзакции.
        db.execute(text("SET TRANSACTION READ ONLY"))
        db.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))

        start = time.perf_counter()
        result = db.execute(text(normalized))
        columns = list(result.keys())
        # +1 строка, чтобы понять, обрезали ли выдачу.
        fetched = result.mappings().fetchmany(limit + 1)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
    except SQLAlchemyError as exc:
        db.rollback()
        # orig несёт человекочитаемое сообщение Postgres (напр. про read-only).
        message = str(getattr(exc, "orig", exc)).strip()
        raise ReportQueryError(message or "Ошибка выполнения запроса") from exc

    db.rollback()

    truncated = len(fetched) > limit
    rows = [dict(row) for row in fetched[:limit]]
    return ReportResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        elapsed_ms=elapsed_ms,
    )
