"""Заявки «обновить по ссылке» в админке

Админ вставляет ссылку на профиль, протокол или список стартов локации
(5 вёрст / S95), система перечитывает источник вне очереди батчей и пишет
сюда ход работы и итог: что добавилось, поправилось, удалилось.

Revision ID: 086_admin_resync_requests
Revises: 085_user_tourism_platforms
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "086_admin_resync_requests"
down_revision = "085_user_tourism_platforms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_resync_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("platform_code", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("input_url", sa.Text(), nullable=False),
        sa.Column("target", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("steps", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("celery_task_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_admin_resync_requests_created_at", "admin_resync_requests", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_admin_resync_requests_created_at", table_name="admin_resync_requests")
    op.drop_table("admin_resync_requests")
