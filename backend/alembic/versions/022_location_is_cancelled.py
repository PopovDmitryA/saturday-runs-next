"""Add is_cancelled flag to locations."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "022_location_is_cancelled"
down_revision = "021_protocol_sync_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "locations",
        sa.Column("is_cancelled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("locations", "is_cancelled")
