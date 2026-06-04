"""user display_name_customized flag

Revision ID: 010_display_custom
Revises: 009_events_loc_date
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "010_display_custom"
down_revision = "009_events_loc_date"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("display_name_customized", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("users", "display_name_customized")
