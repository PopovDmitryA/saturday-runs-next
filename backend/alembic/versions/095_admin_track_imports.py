"""Импорт треков из админки: сессия загрузки с предпросмотром

Админ приносит чужие треки (архив, файлы, ссылки), указывает, чьи они, и
сначала смотрит предпросмотр: что распозналось и к каким пробежкам ляжет.
Треки создаются сразу, но со статусом preview — в кабинет участника они
попадают только после подтверждения.

Revision ID: 095_admin_track_imports
Revises: 094_run_tracks
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "095_admin_track_imports"
down_revision = "094_run_tracks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_track_imports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Чьи это пробежки: в чей кабинет треки лягут после подтверждения.
        sa.Column("target_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        # archive | files | links
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        # collecting | previewing | applied | discarded | error
        sa.Column("status", sa.String(length=16), nullable=False, server_default="collecting"),
        # Сколько источников всего и сколько уже разобрано: страница обрабатывает
        # архив порциями, чтобы длинная выгрузка не упиралась в таймаут.
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        # Что не разобралось: [{"name": ..., "reason": ...}]
        sa.Column("problems", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        # Очередь необработанных источников: имена файлов во временной папке
        # или ссылки на активности.
        sa.Column("pending", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_admin_track_imports_created_at", "admin_track_imports", ["created_at"])

    op.add_column(
        "run_tracks",
        sa.Column(
            "import_batch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("admin_track_imports.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Кто загрузил трек, если это сделал не сам участник.
    op.add_column(
        "run_tracks",
        sa.Column(
            "imported_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_run_tracks_import_batch", "run_tracks", ["import_batch_id"])


def downgrade() -> None:
    op.drop_index("ix_run_tracks_import_batch", table_name="run_tracks")
    op.drop_column("run_tracks", "imported_by_user_id")
    op.drop_column("run_tracks", "import_batch_id")
    op.drop_index("ix_admin_track_imports_created_at", table_name="admin_track_imports")
    op.drop_table("admin_track_imports")
