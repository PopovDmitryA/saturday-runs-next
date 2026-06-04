"""user display_name and activate parkrun platform

Revision ID: 008_display_parkrun
Revises: 007_coord_reply
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "008_display_parkrun"
down_revision = "007_coord_reply"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.String(length=128), nullable=True))
    op.add_column("users", sa.Column("telegram_first_name", sa.String(length=128), nullable=True))
    op.add_column("users", sa.Column("telegram_last_name", sa.String(length=128), nullable=True))
    op.execute(sa.text("UPDATE platforms SET is_active = true WHERE code = 'parkrun'"))


def downgrade() -> None:
    op.execute(sa.text("UPDATE platforms SET is_active = false WHERE code = 'parkrun'"))
    op.drop_column("users", "telegram_last_name")
    op.drop_column("users", "telegram_first_name")
    op.drop_column("users", "display_name")
