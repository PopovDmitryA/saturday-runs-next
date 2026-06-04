"""Add planning_location_seen_at to participants."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "024_participant_planning_seen_at"
down_revision = "023_event_summary_extra"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "participants",
        sa.Column("planning_location_seen_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("participants", "planning_location_seen_at")
