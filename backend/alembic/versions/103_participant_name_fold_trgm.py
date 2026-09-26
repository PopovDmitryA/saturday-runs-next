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


def _has_pg_trgm() -> bool:
    return bool(
        op.get_bind().execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")).scalar()
    )


def upgrade() -> None:
    if not _has_pg_trgm():
        log.warning("pg_trgm не установлен — индекс для поиска по имени пропущен")
        return
    with op.get_context().autocommit_block():
        # Прерванный CONCURRENTLY оставляет INVALID-индекс с тем же именем, и
        # IF NOT EXISTS тогда молча ничего не строит. Снимаем такой огрызок.
        op.execute(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE c.relname = 'ix_participants_display_name_fold_trgm' AND NOT i.indisvalid) THEN "
            "EXECUTE 'DROP INDEX ix_participants_display_name_fold_trgm'; "
            "END IF; END $$"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_participants_display_name_fold_trgm "
            "ON participants USING gin (translate(lower(display_name), 'ё', 'е') gin_trgm_ops)"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_participants_display_name_trgm")


def downgrade() -> None:
    if not _has_pg_trgm():
        return
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_participants_display_name_trgm "
            "ON participants USING gin (lower(display_name) gin_trgm_ops)"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_participants_display_name_fold_trgm")
