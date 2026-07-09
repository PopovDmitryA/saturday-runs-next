"""Add protocol_sync_states.source_updated_at — server-side updated_at from S95 activity lists

Revision ID: 036_protocol_source_updated_at
Revises: 035_ratings
Create Date: 2026-07-09
"""

import sqlalchemy as sa
from alembic import op

revision = "036_protocol_source_updated_at"
down_revision = "035_ratings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "protocol_sync_states",
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("protocol_sync_states", "source_updated_at")
