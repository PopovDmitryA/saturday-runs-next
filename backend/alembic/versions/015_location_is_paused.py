"""Add is_paused flag to locations from official CSV imports."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "015_location_is_paused"
down_revision = "014_location_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "locations",
        sa.Column("is_paused", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("locations", "is_paused")
