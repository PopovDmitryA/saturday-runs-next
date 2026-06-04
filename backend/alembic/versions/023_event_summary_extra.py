"""Add summary_extra jsonb to event_summaries for protocol-level stats."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "023_event_summary_extra"
down_revision = "022_location_is_cancelled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "event_summaries",
        sa.Column("summary_extra", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("event_summaries", "summary_extra")
