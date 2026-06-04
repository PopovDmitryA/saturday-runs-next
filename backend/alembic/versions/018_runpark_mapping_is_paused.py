"""Store runpark pause flag in location mappings."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "018_runpark_mapping_is_paused"
down_revision = "017_runpark_location_mappings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runpark_location_mappings",
        sa.Column("is_paused", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("runpark_location_mappings", "is_paused")
