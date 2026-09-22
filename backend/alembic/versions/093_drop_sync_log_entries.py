"""Убрана пустая таблица sync_log_entries

Заведена первой миграцией под построчный журнал синков, но журнал в итоге
пошёл в scheduled_run_logs (metrics/errors JSONB) и sync_runs.error_message.
За всё время в неё не записали ни строки: ни писателя, ни читателя в коде
нет (аудит 13.09.2026, SCHEMA-05; на проде 21.09.2026 — 0 строк). Две её
внешние связи на sync_runs и sync_jobs были без индексов: любая будущая
чистка этих таблиц платила бы за них последовательным сканом пустой таблицы.

Revision ID: 093_drop_sync_log_entries
Revises: 092_user_avatar_exif
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "093_drop_sync_log_entries"
down_revision = "092_user_avatar_exif"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("sync_log_entries")
    op.execute("DROP TYPE IF EXISTS sync_log_level_enum")


def downgrade() -> None:
    level = postgresql.ENUM("info", "warning", "error", "debug", name="sync_log_level_enum", create_type=False)
    level.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "sync_log_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("sync_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sync_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("level", level, nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["sync_job_id"], ["sync_jobs.id"]),
        sa.ForeignKeyConstraint(["sync_run_id"], ["sync_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
