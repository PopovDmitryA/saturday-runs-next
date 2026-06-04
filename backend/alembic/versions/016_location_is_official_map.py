"""Mark locations imported from official CSV map dumps."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "016_location_is_official_map"
down_revision = "015_location_is_paused"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "locations",
        sa.Column("is_official_map", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("locations", "is_official_map")
