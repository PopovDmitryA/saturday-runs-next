"""Особые награды, выданные руками

Челленджи и клубы считаются из протоколов на лету. Особая награда — решение
админа о конкретном человеке: первым поводом стал тысячный участник сайта
(16.10.2026). Вид награды — код из app/award_kinds.py, название и значок
живут там же. awarded_on — дата события, а не выдачи.

Награда личная: ON DELETE CASCADE от users (удаление профиля уносит её без
отдельного шага), выдавший — SET NULL.

Revision ID: 111_user_awards
Revises: 110_location_status_override
Create Date: 2026-10-20
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "111_user_awards"
down_revision = "110_location_status_override"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_awards",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("awarded_on", sa.Date(), nullable=False),
        sa.Column(
            "granted_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "code", name="uq_user_awards_user_code"),
    )
    op.create_index("ix_user_awards_code", "user_awards", ["code"])


def downgrade() -> None:
    op.drop_index("ix_user_awards_code", table_name="user_awards")
    op.drop_table("user_awards")
