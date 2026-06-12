"""user profile_private flag for future participant search opt-out"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "027_user_profile_private"
down_revision = "026_profile_fetch_pending"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("profile_private", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("users", "profile_private")
