"""User auto-sync preferences and daily login sync timestamp."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "012_user_auto_sync"
down_revision = "011_finish_time_names"
branch_labels = None
depends_on = None

DEFAULT_AUTO_SYNC = '{"five_verst": false, "s95": false, "parkrun": false}'


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("auto_sync_by_platform", postgresql.JSONB(), nullable=False, server_default=DEFAULT_AUTO_SYNC),
    )
    op.add_column(
        "users",
        sa.Column("last_login_auto_sync_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "last_login_auto_sync_at")
    op.drop_column("users", "auto_sync_by_platform")
