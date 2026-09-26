"""Поиск по имени без различия «е»/«ё»: триграммный индекс на свёрнутое имя

Поиск людей (онбординг и поиск по сайту) сравнивал lower(display_name) с
запросом буква в букву. Кто записан в протоколе через «ё» (Фёдоров, Семёнов,
Королёв), не находился по «Федоров», а сайт вдобавок сам менял «ё» на «е»
перед отправкой — так что таких людей не находило вообще ничего.

Теперь обе стороны сравнения сворачиваются одинаково:
translate(lower(display_name), 'ё', 'е') LIKE '%федоров%'. Старый индекс по
lower(display_name) такое условие не обслуживает, поэтому заводим индекс ровно
на новое выражение, а старый снимаем: кроме apply_name_filters, им никто не
пользовался (ILIKE в админке по display_name его и так не брал), а
поддерживается он на каждом upsert участника — самом горячем пути записи.

Индексы строятся и снимаются CONCURRENTLY в autocommit-блоке, как в миграции
090: прод деплоится с работающими воркерами, блокировать participants на
время построения нельзя. pg_trgm уже стоит с миграции 068; если прав на
расширение нет (нестандартная база), индекс пропускаем, а не валим деплой —
поиск просто останется на seq scan, как и было задумано в 068.

Revision ID: 103_participant_name_fold_trgm
Revises: 102_search_query_log
Create Date: 2026-09-26
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from alembic import op

revision = "103_participant_name_fold_trgm"
down_revision = "102_search_query_log"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")


FOLD_INDEX = "ix_participants_display_name_fold_trgm"
OLD_INDEX = "ix_participants_display_name_trgm"


def _has_pg_trgm() -> bool:
    return bool(
        op.get_bind().execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")).scalar()
    )


def _drop_if_invalid(index_name: str) -> None:
    """Снять огрызок прерванного CREATE INDEX CONCURRENTLY — тоже CONCURRENTLY.

    Прерванный CONCURRENTLY (упал SSH на деплое, отмена, дедлок) оставляет
    INVALID-индекс с тем же именем, и IF NOT EXISTS тогда молча ничего не
    строит. Снимать его обычным DROP INDEX нельзя: он берёт ACCESS EXCLUSIVE
    на всю таблицу participants, встаёт в очередь за самой долгой транзакцией,
    которая её трогает (прогревы, домашние воркеры), — и все чтения и записи
    участников сайта ждут уже за ним. DROP INDEX CONCURRENTLY внутри DO-блока
    Postgres не пускает, поэтому проверка — здесь, в Python, а сам DROP —
    отдельной командой в autocommit-блоке (ревью 26.09.2026, MIG-1).
    """
    invalid = op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE c.relname = :name AND NOT i.indisvalid"
        ),
        {"name": index_name},
    ).scalar()
    if invalid:
        log.warning("Индекс %s остался INVALID от прерванной сборки — снимаем и строим заново", index_name)
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}")


def upgrade() -> None:
    if not _has_pg_trgm():
        log.warning("pg_trgm не установлен — индекс для поиска по имени пропущен")
        return
    with op.get_context().autocommit_block():
        _drop_if_invalid(FOLD_INDEX)
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {FOLD_INDEX} "
            "ON participants USING gin (translate(lower(display_name), 'ё', 'е') gin_trgm_ops)"
        )
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {OLD_INDEX}")


def downgrade() -> None:
    if not _has_pg_trgm():
        return
    with op.get_context().autocommit_block():
        _drop_if_invalid(OLD_INDEX)
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {OLD_INDEX} "
            "ON participants USING gin (lower(display_name) gin_trgm_ops)"
        )
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {FOLD_INDEX}")
